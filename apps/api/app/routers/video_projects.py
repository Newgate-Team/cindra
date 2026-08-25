import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.tasks import run_generation_job
from app.content_pipeline.video_studio import (
    VideoStudioFailedError,
    extract_illustration_prompts,
    generate_brief_files,
    generate_script_text,
)
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    GenerationContentType,
    GenerationJob,
    GenerationStatus,
    UsageEventType,
    User,
    VideoProject,
)
from app.schemas import (
    IllustrationOut,
    VideoProjectCreate,
    VideoProjectOut,
    VideoProjectUpdate,
    VideoStyleOut,
)
from app.usage import check_usage_limit, record_usage
from app.video_styles import VIDEO_STYLES

router = APIRouter(prefix="/video-projects", tags=["video-projects"])

# Uploads are read into memory before the R2 put -- cap them well below
# anything that could hurt the worker. A finished vertical short at
# 1080p lands way under this.
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_ALLOWED_VIDEO_MIME = {"video/mp4", "video/quicktime", "video/webm"}


def _owned_project(db: Session, project_id: str, user: User) -> VideoProject:
    try:
        key = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден"
        ) from None
    project = db.get(VideoProject, key)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден")
    return project


def _project_status(project: VideoProject, job: GenerationJob | None) -> str:
    if project.video_url or (
        job is not None and job.status == GenerationStatus.completed
    ):
        return "video_ready"
    if project.brief_files:
        return "brief_ready"
    if project.script:
        return "script_ready"
    return "draft"


def _has_illustrations_in_flight(project: VideoProject, db: Session) -> bool:
    for job_id in project.illustration_job_ids or []:
        job = db.get(GenerationJob, uuid.UUID(job_id))
        if job is not None and job.status in (
            GenerationStatus.queued,
            GenerationStatus.processing,
        ):
            return True
    return False


def _illustrations_out(
    project: VideoProject, db: Session, jobs: dict[uuid.UUID, GenerationJob] | None = None
) -> list[IllustrationOut] | None:
    if not project.illustration_job_ids:
        return None
    illustrations = []
    for job_id in project.illustration_job_ids:
        key = uuid.UUID(job_id)
        job = jobs.get(key) if jobs is not None else db.get(GenerationJob, key)
        if job is None:
            continue
        output = job.output_payload or {}
        illustrations.append(
            IllustrationOut(
                prompt=job.input_payload.get("topic", ""),
                status=job.status,
                image_url=output.get("image_url"),
                error_message=job.error_message,
            )
        )
    return illustrations


def _to_out(
    project: VideoProject, db: Session, jobs: dict[uuid.UUID, GenerationJob] | None = None
) -> VideoProjectOut:
    """`jobs` is an optional prefetched {id: job} map -- the list
    endpoint loads every linked job in one query instead of one per
    illustration per project (CIN-139)."""
    job: GenerationJob | None = None
    if project.video_generation_job_id is not None:
        job = (
            jobs.get(project.video_generation_job_id)
            if jobs is not None
            else db.get(GenerationJob, project.video_generation_job_id)
        )
    video_url = project.video_url
    if video_url is None and job is not None and job.output_payload:
        video_url = job.output_payload.get("video_url")
    return VideoProjectOut(
        id=project.id,
        topic=project.topic,
        brand_guide=project.brand_guide,
        script=project.script,
        style=project.style,
        brief_files=project.brief_files,
        video_url=video_url,
        video_status=job.status if job is not None else None,
        video_error=job.error_message if job is not None else None,
        illustrations=_illustrations_out(project, db, jobs),
        status=_project_status(project, job),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/styles", response_model=list[VideoStyleOut])
def list_styles(current_user: User = Depends(get_current_user)) -> list[VideoStyleOut]:
    return [
        VideoStyleOut(
            id=style_id,
            title=style["title"],
            description=style["description"],
            produces=style["produces"],
            generates_illustrations=style["generates_illustrations"],
        )
        for style_id, style in VIDEO_STYLES.items()
    ]


@router.get("", response_model=list[VideoProjectOut])
def list_projects(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[VideoProjectOut]:
    projects = db.scalars(
        select(VideoProject)
        .where(VideoProject.user_id == current_user.id)
        .order_by(VideoProject.created_at.desc())
    ).all()
    job_ids: set[uuid.UUID] = set()
    for project in projects:
        if project.video_generation_job_id is not None:
            job_ids.add(project.video_generation_job_id)
        job_ids.update(uuid.UUID(i) for i in project.illustration_job_ids or [])
    jobs = (
        {
            job.id: job
            for job in db.scalars(
                select(GenerationJob).where(GenerationJob.id.in_(job_ids))
            ).all()
        }
        if job_ids
        else {}
    )
    return [_to_out(project, db, jobs) for project in projects]


@router.post("", response_model=VideoProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: VideoProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    project = VideoProject(
        user_id=current_user.id, topic=payload.topic, brand_guide=payload.brand_guide
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _to_out(project, db)


@router.get("/{project_id}", response_model=VideoProjectOut)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    return _to_out(_owned_project(db, project_id, current_user), db)


@router.patch("/{project_id}", response_model=VideoProjectOut)
def update_project(
    project_id: str,
    payload: VideoProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    project = _owned_project(db, project_id, current_user)
    changes = payload.model_dump(exclude_unset=True)
    if "style" in changes and changes["style"] is not None and changes["style"] not in VIDEO_STYLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неизвестный стиль: {changes['style']}",
        )
    for field, value in changes.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return _to_out(project, db)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    project = _owned_project(db, project_id, current_user)
    db.delete(project)
    db.commit()


@router.post("/{project_id}/script", response_model=VideoProjectOut)
def generate_script(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    """Generate (or regenerate) the project's script. Synchronous --
    a flash-lite text call answers in seconds, so no job queue; counted
    against the text-generation limit like any other text generation."""
    project = _owned_project(db, project_id, current_user)
    check_usage_limit(db, current_user, UsageEventType.generation, GenerationContentType.text)
    try:
        script = generate_script_text(project.topic, project.brand_guide)
    except TransientGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except VideoStudioFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    record_usage(db, current_user, UsageEventType.generation, GenerationContentType.text)
    project.script = script
    db.commit()
    db.refresh(project)
    return _to_out(project, db)


@router.post("/{project_id}/brief", response_model=VideoProjectOut)
def generate_brief(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    project = _owned_project(db, project_id, current_user)
    if not project.script:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сначала сгенерируйте сценарий",
        )
    if not project.style:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Сначала выберите стиль"
        )
    if VIDEO_STYLES[project.style]["produces"] != "brief":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот стиль генерирует готовый ролик, а не бриф",
        )
    check_usage_limit(db, current_user, UsageEventType.generation, GenerationContentType.text)
    try:
        brief_files = generate_brief_files(
            project.topic, project.script, project.style, project.brand_guide
        )
    except TransientGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except VideoStudioFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    record_usage(db, current_user, UsageEventType.generation, GenerationContentType.text)
    project.brief_files = brief_files
    db.commit()
    db.refresh(project)
    return _to_out(project, db)


@router.post("/{project_id}/illustrations", response_model=VideoProjectOut)
def generate_illustrations(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    """Generate the brief's illustrations through the image pipeline
    (CIN-137) -- blocks/cartoon styles only, whose production plan
    lists ready generation prompts. The prompt list is extracted
    first (no charge on failure), then the whole set is checked
    atomically against the image-generation limit: either every
    illustration fits, or nothing is charged/started."""
    project = _owned_project(db, project_id, current_user)
    style = VIDEO_STYLES.get(project.style or "")
    if style is None or not style["generates_illustrations"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Автогенерация иллюстраций доступна для стилей «По блокам» и «Мультяшный»",
        )
    if not project.brief_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Сначала сгенерируйте бриф"
        )
    # CIN-139: a second click while the first batch is still running
    # would charge the image quota again and orphan the running jobs.
    if _has_illustrations_in_flight(project, db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Иллюстрации ещё генерируются — дождитесь окончания",
        )
    production = next(
        (f for f in project.brief_files if f["filename"].startswith("production")),
        None,
    )
    production_content = (
        production["content"]
        if production is not None
        else "\n\n".join(f["content"] for f in project.brief_files)
    )
    try:
        prompts = extract_illustration_prompts(production_content)
    except TransientGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except VideoStudioFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    check_usage_limit(
        db,
        current_user,
        UsageEventType.generation,
        GenerationContentType.image,
        count=len(prompts),
    )
    jobs = []
    for prompt in prompts:
        job = GenerationJob(
            user_id=current_user.id,
            content_type=GenerationContentType.image,
            input_payload={
                "topic": prompt,
                "image_kind": "illustration",
                "brand_guide": project.brand_guide,
                "content_kind": "post",
            },
        )
        db.add(job)
        jobs.append(job)
    db.flush()
    project.illustration_job_ids = [str(job.id) for job in jobs]
    record_usage(
        db,
        current_user,
        UsageEventType.generation,
        GenerationContentType.image,
        count=len(jobs),
    )
    for job in jobs:
        run_generation_job.delay(str(job.id))
    db.refresh(project)
    return _to_out(project, db)


@router.post("/{project_id}/video-generation", response_model=VideoProjectOut)
def start_video_generation(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    """veo_auto style only: generate the finished clip from the script
    through the existing Veo pipeline (celery GenerationJob). The
    wizard polls GET /video-projects/{id} -- the linked job's status
    and result are folded into the project response."""
    project = _owned_project(db, project_id, current_user)
    if project.style != "veo_auto":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Автогенерация ролика доступна только для стиля «Полное авто (Veo)»",
        )
    if not project.script:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сначала сгенерируйте сценарий",
        )
    # CIN-139: Veo generation is the single most expensive call in the
    # app -- never start a second one while the first is still running.
    if project.video_generation_job_id is not None:
        running = db.get(GenerationJob, project.video_generation_job_id)
        if running is not None and running.status in (
            GenerationStatus.queued,
            GenerationStatus.processing,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ролик уже генерируется — дождитесь окончания",
            )
    check_usage_limit(db, current_user, UsageEventType.generation, GenerationContentType.video)
    job = GenerationJob(
        user_id=current_user.id,
        content_type=GenerationContentType.video,
        # video_generator builds its own prompt from topic/brand_guide;
        # the script (capped -- Veo prompts have no use for many pages)
        # carries the creative direction.
        input_payload={
            "topic": f"{project.topic}. Сценарий ролика: {project.script[:2000]}",
            "brand_guide": project.brand_guide,
            "content_kind": "post",
        },
    )
    db.add(job)
    db.flush()
    project.video_generation_job_id = job.id
    # commits the job, the link and the usage event together
    record_usage(db, current_user, UsageEventType.generation, GenerationContentType.video)
    run_generation_job.delay(str(job.id))
    db.refresh(project)
    return _to_out(project, db)


@router.post("/{project_id}/video", response_model=VideoProjectOut)
async def upload_video(
    project_id: str,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VideoProjectOut:
    """Upload the finished, externally edited video (shot from the
    brief) into the project -- from here the existing /posts flow
    publishes it to any connected account. The user's own file, so no
    generation-limit charge."""
    project = _owned_project(db, project_id, current_user)
    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_VIDEO_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ожидается видеофайл (mp4, mov или webm)",
        )
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 200 МБ",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    extension = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}[
        content_type
    ]
    project.video_url = upload_bytes(data, content_type, extension)
    db.commit()
    db.refresh(project)
    return _to_out(project, db)
