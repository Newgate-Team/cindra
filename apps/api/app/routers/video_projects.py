import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.tasks import run_generation_job
from app.content_pipeline.video_studio import (
    VideoStudioFailedError,
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
    VideoProjectCreate,
    VideoProjectOut,
    VideoProjectUpdate,
    VideoStyleOut,
)
from app.usage import enforce_and_record_usage
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


def _to_out(project: VideoProject, db: Session) -> VideoProjectOut:
    job: GenerationJob | None = None
    if project.video_generation_job_id is not None:
        job = db.get(GenerationJob, project.video_generation_job_id)
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
    return [_to_out(project, db) for project in projects]


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
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, GenerationContentType.text
    )
    try:
        project.script = generate_script_text(project.topic, project.brand_guide)
    except TransientGenerationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except VideoStudioFailedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, GenerationContentType.text
    )
    try:
        project.brief_files = generate_brief_files(
            project.topic, project.script, project.style, project.brand_guide
        )
    except TransientGenerationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except VideoStudioFailedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.commit()
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
    enforce_and_record_usage(
        db, current_user, UsageEventType.generation, GenerationContentType.video
    )
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
    db.commit()
    db.refresh(project)
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
