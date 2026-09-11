import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_pipeline.publish_matrix import (
    InvalidGenerationTargetError,
    validate_generation_target,
)
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    GenerationContentType,
    GenerationJob,
    Post,
    PostStatus,
    SocialAccount,
    UsageEvent,
    UsageEventType,
    User,
)
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.scheduler.tasks import publish_post
from app.schemas import PostCreate, PostOut, PostUpdate
from app.usage import check_usage_limit

router = APIRouter(prefix="/posts", tags=["posts"])


def _post_out(post: Post, account: SocialAccount) -> PostOut:
    return PostOut(
        id=post.id,
        social_account_id=post.social_account_id,
        text=post.text,
        image_url=post.image_url,
        video_url=post.video_url,
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


def _existing_posts_by_account(
    db: Session, generation_job_id: uuid.UUID | None, social_account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Post]:
    """CIN-122: (generation_job_id, social_account_id) uniquely
    identifies "this generated content, published to this account" --
    only meaningful when generation_job_id is set (manually-composed
    posts have nothing to dedupe against)."""
    if generation_job_id is None:
        return {}
    existing = db.scalars(
        select(Post).where(
            Post.generation_job_id == generation_job_id,
            Post.social_account_id.in_(social_account_ids),
        )
    ).all()
    return {p.social_account_id: p for p in existing}


def _create_new_posts(
    db: Session,
    current_user: User,
    payload: PostCreate,
    new_account_ids: list[uuid.UUID],
    scheduled_for: datetime,
) -> list[Post]:
    # Deliberately not enforce_and_record_usage_bulk -- it commits the
    # usage charge itself, as its own transaction. That would split the
    # charge from the Post insert below into two separately-committed
    # steps, and create_post's IntegrityError retry (which rolls back
    # to undo BOTH together) can only undo whichever of them hasn't
    # committed yet. check_usage_limit(for_update=True) here holds the
    # same CIN-158 lock without committing, so the charge and the Post
    # rows land in the one db.commit() below -- either both happen or
    # neither does.
    check_usage_limit(
        db,
        current_user,
        UsageEventType.publication,
        count=len(new_account_ids),
        for_update=True,
    )
    db.add_all(
        [
            UsageEvent(user_id=current_user.id, event_type=UsageEventType.publication)
            for _ in new_account_ids
        ]
    )
    new_posts = [
        Post(
            user_id=current_user.id,
            social_account_id=account_id,
            generation_job_id=payload.generation_job_id,
            text=payload.text,
            image_url=payload.image_url,
            video_url=payload.video_url,
            content_kind=payload.content_kind,
            platform_options=payload.platform_options,
            scheduled_for=scheduled_for,
        )
        for account_id in new_account_ids
    ]
    db.add_all(new_posts)
    db.commit()
    return new_posts


@router.get("", response_model=Page[PostOut])
def list_posts(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[PostOut]:
    query = (
        select(Post, SocialAccount)
        .join(SocialAccount, Post.social_account_id == SocialAccount.id)
        .where(Post.user_id == current_user.id)
        .order_by(Post.scheduled_for.desc())
    )
    rows, total = paginate(db, query, limit, offset)
    items = [_post_out(post, account) for post, account in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=list[PostOut], status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PostOut]:
    # Fan-out publish (CIN-106): one call creates one Post per target
    # account, all sharing generation_job_id -- batched in one request
    # (rather than one POST /posts per account from the frontend) so
    # the usage-limit check below is atomic across the whole batch,
    # instead of racing/partially succeeding target by target.
    accounts = db.scalars(
        select(SocialAccount).where(SocialAccount.id.in_(payload.social_account_ids))
    ).all()
    accounts_by_id = {a.id: a for a in accounts}
    if set(payload.social_account_ids) - accounts_by_id.keys() or any(
        a.user_id != current_user.id for a in accounts
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Соцаккаунт не найден"
        )

    # Generation no longer requires a target account up front (a user
    # can generate before connecting anything), so content_type/
    # content_kind vs. platform compatibility (publish_matrix.py) is no
    # longer guaranteed to have been checked before this content ever
    # existed -- check it here instead, against whatever accounts were
    # actually picked for this publish. content_type has no column on
    # Post; derived the same way the frontend already decides which
    # media field to send.
    content_type = (
        GenerationContentType.video
        if payload.video_url
        else GenerationContentType.image
        if payload.image_url
        else GenerationContentType.text
    )
    try:
        validate_generation_target(
            {a.platform for a in accounts}, content_type, payload.content_kind
        )
    except InvalidGenerationTargetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    # generation_job_id is a plain client-supplied uuid (schemas.py) --
    # unlike social_account_ids above, nothing checked it belongs to
    # this user, or even exists. Two problems: (1) tagging a post with
    # someone else's job id pollutes the CIN-122 dedup namespace across
    # tenants -- never a content-disclosure IDOR (a Post's own text/
    # image/video always come from this request's own body, never the
    # job), but it breaks the ownership invariant the field is meant to
    # carry; (2) a genuinely nonexistent job id trips the FK constraint
    # on Post.generation_job_id as an IntegrityError -- which the
    # retry below (meant for the uq_post_generation_job_account race)
    # would retry once and then let through unhandled, a confusing 500
    # instead of a clean 404.
    if payload.generation_job_id is not None:
        job = db.get(GenerationJob, payload.generation_job_id)
        if job is None or job.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Задача генерации не найдена"
            )

    _reject_if_in_the_past(payload.scheduled_for)

    # CIN-122: idempotent retry. A dropped HTTP response after a
    # successful create (CIN-120 -- e.g. a mid-request backend restart)
    # is indistinguishable client-side from a failed request, so a
    # retry looks identical to the first attempt -- confirmed in
    # production, one Story published 4x from 4 retries of the same
    # generated content. When this publish is tied to a generation job,
    # (generation_job_id, social_account_id) uniquely identifies "this
    # exact generated content, published to this exact account" --
    # reuse whatever's already there for that pair instead of creating
    # (and re-charging, re-dispatching) a duplicate.
    scheduled_for = payload.scheduled_for or datetime.now(UTC)
    existing_by_account = _existing_posts_by_account(
        db, payload.generation_job_id, payload.social_account_ids
    )
    new_account_ids = [
        account_id for account_id in payload.social_account_ids if account_id not in existing_by_account
    ]

    new_posts: list[Post] = []
    if new_account_ids:
        try:
            new_posts = _create_new_posts(
                db, current_user, payload, new_account_ids, scheduled_for
            )
        except IntegrityError:
            # The above check-then-insert is itself a race: a
            # concurrent retry of this exact request (same
            # generation_job_id, CIN-120's dropped-response scenario --
            # not sequential, genuinely overlapping) can win between our
            # check and this commit. Roll back (undoes the usage charge
            # too, same transaction) and recompute against what's
            # actually committed now, rather than 500ing or -- worse --
            # leaving the usage charge applied with no Post to show for
            # it. One retry: by construction, existing_by_account is
            # fully current immediately after the rollback, so a second
            # collision here would need a third truly-simultaneous
            # request.
            db.rollback()
            existing_by_account = _existing_posts_by_account(
                db, payload.generation_job_id, payload.social_account_ids
            )
            new_account_ids = [
                account_id
                for account_id in payload.social_account_ids
                if account_id not in existing_by_account
            ]
            if new_account_ids:
                new_posts = _create_new_posts(
                    db, current_user, payload, new_account_ids, scheduled_for
                )

        for post in new_posts:
            db.refresh(post)

        if new_posts and scheduled_for <= datetime.now(UTC):
            # Due now rather than in the future -- dispatch immediately
            # instead of waiting for the next beat tick (up to 60s away,
            # see celery_app.conf.beat_schedule). Each dispatch is fully
            # independent by post_id (see scheduler/tasks.py), so one
            # target failing doesn't affect the others.
            for post in new_posts:
                publish_post.delay(str(post.id))
            for post in new_posts:
                db.refresh(post)

    posts_by_account = {**existing_by_account, **{p.social_account_id: p for p in new_posts}}
    posts = [posts_by_account[account_id] for account_id in payload.social_account_ids]
    return [_post_out(post, accounts_by_id[post.social_account_id]) for post in posts]


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
