from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import GenerationContentType, GenerationJob, GenerationStatus, User
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas import FeedItemOut

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("", response_model=Page[FeedItemOut])
def list_feed(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    # Requires auth (feed is a logged-in feature, not public), but
    # deliberately does NOT filter by user_id below -- this is a shared
    # feed across every user's generations, by design (CIN-109), not a
    # scoping bug like every other list endpoint in this file's siblings.
    # It DOES filter by each generating user's own
    # share_generations_to_feed opt-out (agency accounts default to
    # opted out -- an unreleased client campaign shouldn't be visible to
    # every other user before the client sees it published; see the
    # User model).
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[FeedItemOut]:
    query = (
        select(GenerationJob)
        .join(User, GenerationJob.user_id == User.id)
        .where(
            GenerationJob.status == GenerationStatus.completed,
            GenerationJob.content_type.in_([GenerationContentType.image, GenerationContentType.video]),
            User.share_generations_to_feed.is_(True),
        )
        .order_by(GenerationJob.created_at.desc())
    )
    rows, total = paginate(db, query, limit, offset)
    items = [
        FeedItemOut(
            id=job.id,
            content_type=job.content_type,
            image_url=(job.output_payload or {}).get("image_url"),
            video_url=(job.output_payload or {}).get("video_url"),
            # CIN-116: prefer the generated caption (CIN-114) over the
            # raw prompt -- falls back to the prompt since caption
            # generation is best-effort and may be missing.
            caption=(job.output_payload or {}).get("text")
            or (job.input_payload or {}).get("topic", ""),
            created_at=job.created_at,
        )
        for (job,) in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)
