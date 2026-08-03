from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Post, SocialAccount, UsageEventType, User
from app.scheduler.tasks import publish_post
from app.schemas import PostCreate, PostOut
from app.usage import enforce_and_record_usage

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=list[PostOut])
def list_posts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Post]:
    return list(
        db.scalars(
            select(Post)
            .where(Post.user_id == current_user.id)
            .order_by(Post.scheduled_for.desc())
        )
    )


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Post:
    account = db.get(SocialAccount, payload.social_account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Соцаккаунт не найден"
        )

    enforce_and_record_usage(db, current_user, UsageEventType.publication)

    scheduled_for = payload.scheduled_for or datetime.now(UTC)
    post = Post(
        user_id=current_user.id,
        social_account_id=account.id,
        generation_job_id=payload.generation_job_id,
        text=payload.text,
        image_url=payload.image_url,
        content_kind=payload.content_kind,
        scheduled_for=scheduled_for,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    if scheduled_for <= datetime.now(UTC):
        # Due now rather than in the future -- dispatch immediately
        # instead of waiting for the next beat tick (up to 60s away,
        # see celery_app.conf.beat_schedule).
        publish_post.delay(str(post.id))
        db.refresh(post)

    return post


@router.get("/{post_id}", response_model=PostOut)
def get_post(
    post_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Post:
    post = db.get(Post, post_id)
    if post is None or post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Публикация не найдена"
        )
    return post
