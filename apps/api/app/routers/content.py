from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content_pipeline.attachments import (
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
    classify_attachment,
    downscale_image_for_background,
    downscale_image_for_context,
)
from app.content_pipeline.image_generator import nano_banana_image_generator
from app.content_pipeline.layout_renderer import (
    LayoutFontMissingError,
    LayoutRenderError,
    render_layout,
    render_sample,
)
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.publish_matrix import (
    InvalidGenerationTargetError,
    validate_generation_target,
)
from app.content_pipeline.tasks import run_generation_job
from app.db import get_db
from app.deps import get_current_user, require_admin
from app.image_templates import IMAGE_TEMPLATES
from app.layout_templates import LAYOUT_TEMPLATES
from app.models import (
    GenerationJob,
    ImageTemplatePreview,
    SocialAccount,
    UsageEventType,
    User,
)
from app.schemas import (
    AttachmentOut,
    GenerationJobOut,
    GenerationRequest,
    ImageTemplateOut,
    ImageTemplatePreviewsOut,
    LayoutBackgroundOut,
    LayoutRenderOut,
    LayoutRenderRequest,
    LayoutTemplateOut,
)
from app.usage import check_usage_limit, enforce_and_record_usage, record_usage

router = APIRouter(prefix="/content", tags=["content"])


@router.post("/attachment", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile, current_user: User = Depends(get_current_user)
) -> AttachmentOut:
    """Upload an optional context file (CIN-97) for a later /content/generate
    call -- separate from generation itself (which is async/queued) since
    upload validation and the R2 PUT are both fast, synchronous operations.
    Free on every tier: this isn't a metered UsageEvent, just storage.
    """
    data = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    try:
        # Size cap is enforced on the original upload, before any
        # downscaling below -- otherwise it'd be trivial to dodge the
        # cap with an image that only becomes small after resizing.
        attachment_type = classify_attachment(mime_type, len(data))
    except UnsupportedAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from None

    extension = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "bin"
    if attachment_type == "image":
        # CIN-98: shrink to Gemini's single-tile bound once here, at
        # upload time, rather than on every later generation that
        # reads this attachment back.
        try:
            data, mime_type = downscale_image_for_context(data)
        except UnsupportedAttachmentError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        extension = "jpg"
    url = upload_bytes(data, mime_type, extension)
    return AttachmentOut(url=url, attachment_type=attachment_type, mime_type=mime_type)


@router.post(
    "/generate", response_model=GenerationJobOut, status_code=status.HTTP_202_ACCEPTED
)
def generate_content(
    payload: GenerationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationJob:
    # Target accounts are chosen up front (CIN-106) -- content_type/
    # content_kind must be publishable to all of them at once, checked
    # here (before spending any generation budget) rather than only
    # failing later at actual publish time.
    accounts = db.scalars(
        select(SocialAccount).where(SocialAccount.id.in_(payload.target_account_ids))
    ).all()
    found_ids = {a.id for a in accounts}
    missing = set(payload.target_account_ids) - found_ids
    if missing or any(a.user_id != current_user.id for a in accounts):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Соцаккаунт не найден"
        )

    platforms = {a.platform for a in accounts}
    try:
        validate_generation_target(platforms, payload.content_type, payload.content_kind)
    except InvalidGenerationTargetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    # content_type defaults to text. All three content types now have
    # real generators registered (text: CIN-53, image: CIN-54, video:
    # CIN-55) -- nothing about this endpoint or the queue needed to
    # change once they were. Limit is per-format (CIN-60), not a
    # single "generations" total -- text/image/video cost four orders
    # of magnitude apart (see CIN-59).
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, payload.content_type
    )

    input_payload = payload.model_dump(mode="json")
    # Text generation's tone/format guidance is keyed by a single
    # platform (see prompts.py); image/video generation don't read
    # platform at all. Rather than the bigger scope of generating a
    # distinct text variant per target platform, tone is derived from
    # the first-selected target account.
    first_account = min(accounts, key=lambda a: payload.target_account_ids.index(a.id))
    input_payload["platform"] = first_account.platform.value

    job = GenerationJob(
        user_id=current_user.id,
        content_type=payload.content_type,
        input_payload=input_payload,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    run_generation_job.delay(str(job.id))

    # In production this dispatch is fire-and-forget (async worker,
    # job still "queued" in the response). In tests task_always_eager
    # runs it synchronously *on a separate DB session/connection*
    # inside the task, so this session's copy of `job` is stale until
    # re-queried -- refresh() always re-SELECTs regardless of prior
    # expiration state, so it picks up whatever the task committed.
    db.refresh(job)
    return job


# CIN-148: like the catalog below, these sit before GET /{job_id} so
# the catch-all doesn't swallow their paths as job ids.
@router.get("/layout-templates", response_model=list[LayoutTemplateOut])
def list_layout_templates(
    current_user: User = Depends(get_current_user),
) -> list[LayoutTemplateOut]:
    """Catalog of code-rendered templates: what each one is and which
    fields the user fills in."""
    return [
        LayoutTemplateOut(
            id=template_id,
            title=t["title"],
            description=t["description"],
            supports_image=t["supports_image"],
            slots=t["slots"],
        )
        for template_id, t in LAYOUT_TEMPLATES.items()
    ]


@router.get("/layout-templates/{template_id}/preview")
def preview_layout_template(
    template_id: str,
    canvas_format: str = "square",
    theme: str = "dark",
    current_user: User = Depends(get_current_user),
) -> Response:
    """Gallery thumbnail: the template filled with demo copy.

    Rendered per request rather than stored -- it takes tens of
    milliseconds, costs nothing, and never goes stale when a template
    changes. Not metered for the same reason.
    """
    try:
        png = render_sample(template_id, canvas_format, theme=theme)
    except LayoutRenderError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LayoutFontMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return Response(
        content=png,
        media_type="image/png",
        # Same demo copy every time -- let the browser keep it.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post(
    "/layout-background",
    response_model=LayoutBackgroundOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_layout_background(
    file: UploadFile, current_user: User = Depends(get_current_user)
) -> LayoutBackgroundOut:
    """Upload a background for a template that supports one (CIN-151).

    Separate from /content/attachment because that path shrinks images
    to 384px for the model's tile budget (CIN-98) -- fine as context,
    visibly soft behind a 1080x1920 card. Like attachments, it isn't
    metered: only the render itself is.
    """
    if not get_settings().r2_account_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Медиа-хранилище не настроено на сервере",
        )
    data = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    try:
        attachment_type = classify_attachment(mime_type, len(data))
    except UnsupportedAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from None
    if attachment_type != "image":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Подложкой может быть только изображение",
        )
    try:
        data, mime_type = downscale_image_for_background(data)
    except UnsupportedAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return LayoutBackgroundOut(background_url=upload_bytes(data, mime_type, "jpg"))


@router.post("/layout-render", response_model=LayoutRenderOut)
def render_layout_template(
    payload: LayoutRenderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LayoutRenderOut:
    """Render a template with the user's own text and store it in R2.

    Synchronous: no model call, so the result is known within the
    request. Per CIN-139 the quota is therefore checked first and only
    recorded once the render actually succeeded.
    """
    # Without R2 the upload below dies deep inside boto3 with a bare
    # ValueError ("Invalid endpoint: https://.r2.cloudflarestorage.com")
    # and the user just sees a 500. Checked up front instead, the same
    # way /auth/google reports its own missing config (CIN-133).
    if not get_settings().r2_account_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Медиа-хранилище не настроено на сервере",
        )
    check_usage_limit(db, current_user, UsageEventType.layout_render)
    try:
        png = render_layout(
            payload.template_id,
            payload.canvas_format,
            payload.values,
            theme=payload.theme,
            accent=payload.accent,
            background_url=payload.background_url,
        )
    except LayoutRenderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LayoutFontMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    image_url = upload_bytes(png, "image/png", "png")
    record_usage(db, current_user, UsageEventType.layout_render)
    return LayoutRenderOut(image_url=image_url)


# CIN-143: registered before GET /{job_id} -- that catch-all would
# otherwise swallow "image-templates" as a job id.
@router.get("/image-templates", response_model=list[ImageTemplateOut])
def list_image_templates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ImageTemplateOut]:
    """Image template catalog for the «Посты» page -- the frontend
    renders whatever is here (single source of truth, like
    /video-projects/styles)."""
    previews = {
        row.template_id: row.preview_url
        for row in db.scalars(select(ImageTemplatePreview)).all()
    }
    return [
        ImageTemplateOut(
            id=template_id,
            title=t["title"],
            description=t["description"],
            preview_url=previews.get(template_id),
        )
        for template_id, t in IMAGE_TEMPLATES.items()
    ]


@router.post("/image-templates/previews", response_model=ImageTemplatePreviewsOut)
def generate_image_template_previews(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ImageTemplatePreviewsOut:
    """Generate one example image per AI template and store it (CIN-150).

    Staff-only and deliberately manual: this spends a real image
    generation per template, so it runs when the catalog changes, not
    on a schedule and never on a user's quota. Failures are reported
    per template instead of aborting -- one template's refusal must not
    cost the whole run.
    """
    generated: list[str] = []
    failed: dict[str, str] = {}
    for template_id, template in IMAGE_TEMPLATES.items():
        try:
            # Goes through the ordinary user path -- enhancer included
            # (CIN-142) -- so the example shows what someone actually
            # gets, not a differently-built approximation.
            result = nano_banana_image_generator(
                {"topic": template["preview_topic"], "image_template": template_id}
            )
        except Exception as exc:  # noqa: BLE001 -- reported per template below
            failed[template_id] = str(exc)[:200]
            continue
        # Upsert rather than merge(): regenerating replaces the
        # stored example, and ON CONFLICT keeps that correct even if two
        # staff runs overlap.
        db.execute(
            pg_insert(ImageTemplatePreview)
            .values(template_id=template_id, preview_url=result["image_url"])
            .on_conflict_do_update(
                index_elements=[ImageTemplatePreview.template_id],
                set_={
                    "preview_url": result["image_url"],
                    "generated_at": datetime.now(UTC),
                },
            )
        )
        generated.append(template_id)
    db.commit()
    return ImageTemplatePreviewsOut(generated=generated, failed=failed)


@router.get("/{job_id}", response_model=GenerationJobOut)
def get_generation_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена"
        )
    return job
