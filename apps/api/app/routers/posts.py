from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Post, PostStatus, SocialAccount, UsageEventType, User
from app.scheduler.tasks import publish_post
from app.schemas import PostCreate, PostOut, PostUpdate
from app.usage import enforce_and_record_usage

router = APIRouter(prefix="/posts", tags=["posts"])


def _post_out(post: Post, account: SocialAccount) -> PostOut:
    return PostOut(
        id=post.id,
        social_account_id=post.social_account_id,
        text=post.text,
        image_url=post.image_url,
        content_kind=post.content_kind,
        status=post.status,
        scheduled_for=post.scheduled_for,
        platform_message_id=post.platform_message_id,
        error_message=post.error_message,
        created_at=post.created_at,
        published_at=post.published_at,
        platform=account.platform,
        account_label=account.display_name or account.external_account_id,
    )


def _reject_if_in_the_past(scheduled_for: datetime | None) -> None:
    """`None` means "not provided" (create: publish now: update: no
    change) -- only an explicit past datetime is rejected."""
    if scheduled_for is not None and scheduled_for < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Дата публикации не может быть в прошлом",
        )


@router.get("", response_model=list[PostOut])
def list_posts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[PostOut]:
    rows = db.execute(
        select(Post, SocialAccount)
        .join(SocialAccount, Post.social_account_id == SocialAccount.id)
        .where(Post.user_id == current_user.id)
        .order_by(Post.scheduled_for.desc())
    ).all()
    return [_post_out(post, account) for post, account in rows]


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    account = db.get(SocialAccount, payload.social_account_id)
    if account is None or account.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Соцаккаунт не найден"
        )

    _reject_if_in_the_past(payload.scheduled_for)
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

    return _post_out(post, account)


@router.get("/{post_id}", response_model=PostOut)
def get_post(
    post_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    post = db.get(Post, post_id)
    if post is None or post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Публикация не найдена"
        )
    return _post_out(post, db.get(SocialAccount, post.social_account_id))


def _get_scheduled_post_owned_by(db: Session, post_id: str, current_user: User) -> Post:
    post = db.get(Post, post_id)
    if post is None or post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Публикация не найдена"
        )
    if post.status != PostStatus.scheduled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Изменить или отменить можно только ещё не опубликованную (scheduled) публикацию",
        )
    return post


@router.patch("/{post_id}", response_model=PostOut)
def update_post(
    post_id: str,
    payload: PostUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostOut:
    post = _get_scheduled_post_owned_by(db, post_id, current_user)
    _reject_if_in_the_past(payload.scheduled_for)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(post, field, value)
    db.commit()
    db.refresh(post)
    return _post_out(post, db.get(SocialAccount, post.social_account_id))


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_post(
    post_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    post = _get_scheduled_post_owned_by(db, post_id, current_user)
    db.delete(post)
    db.commit()
