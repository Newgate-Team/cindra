import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(str, enum.Enum):
    agency = "agency"
    solo = "solo"


class SocialPlatform(str, enum.Enum):
    telegram = "telegram"
    instagram = "instagram"
    facebook = "facebook"
    tiktok = "tiktok"


class SubscriptionTier(str, enum.Enum):
    free = "free"
    pro = "pro"
    business = "business"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    past_due = "past_due"
    canceled = "canceled"


class SubscriptionStore(str, enum.Enum):
    # `none` is what every subscription has until it's actually paid
    # through a real provider -- tier/status still work for
    # enforcement in the meantime (e.g. the default free tier).
    # google_play/app_store were placeholders from before CIN-18
    # settled on CloudPayments (this is a web SaaS, not app-store
    # distributed -- kept for now in case mobile distribution happens
    # later, but not wired to anything).
    none = "none"
    google_play = "google_play"
    app_store = "app_store"
    # No longer used going forward (CIN-18, 2026-08-04: switched to
    # PayPal) -- kept in the enum so historical rows/migrations stay
    # valid, not wired to any code path anymore.
    cloudpayments = "cloudpayments"
    paypal = "paypal"


class UsageEventType(str, enum.Enum):
    generation = "generation"
    publication = "publication"
    # CIN-146: a Seedance clip (CIN-144) costs ~15x a Veo one, so it
    # gets its own small counter instead of eating the video quota.
    # Deliberately an event type rather than a GenerationContentType
    # value: that enum is shared with generation_jobs.content_type and
    # is user-supplied on /content/generate, and a long clip is still
    # an ordinary video everywhere except billing.
    long_video_generation = "long_video_generation"


class GenerationContentType(str, enum.Enum):
    text = "text"
    image = "image"
    video = "video"


class GenerationStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    flagged = "flagged"


class PostStatus(str, enum.Enum):
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # NULL for accounts created through Google sign-in (CIN-133) --
    # they have no password and can only log in via Google.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.solo, nullable=False
    )
    # CIN-147: staff flag for endpoints that expose data about the whole
    # user base (/metrics/summary). Deliberately NOT a UserRole value:
    # `role` is chosen by the user at registration and editable from
    # their profile, so an "admin" role would be self-assignable. This
    # column appears in no request schema -- it's set by hand in the DB.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(SubscriptionTier, name="subscription_tier"),
        default=SubscriptionTier.free,
        nullable=False,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.active,
        nullable=False,
    )
    store: Mapped[SubscriptionStore] = mapped_column(
        Enum(SubscriptionStore, name="subscription_store"),
        default=SubscriptionStore.none,
        nullable=False,
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[UsageEventType] = mapped_column(
        Enum(UsageEventType, name="usage_event_type"), nullable=False
    )
    # Only set for generation events -- text/image/video have costs
    # four orders of magnitude apart (see CIN-59), so limits and usage
    # counts are enforced per format, not as one "generations" total.
    # Null for publication events, which don't have this distinction.
    content_type: Mapped[GenerationContentType | None] = mapped_column(
        Enum(GenerationContentType, name="generation_content_type"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content_type: Mapped[GenerationContentType] = mapped_column(
        Enum(GenerationContentType, name="generation_content_type"), nullable=False
    )
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(GenerationStatus, name="generation_status"),
        default=GenerationStatus.queued,
        nullable=False,
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(4096), nullable=False)
    # Telegram can publish text alone; Instagram's Content Publishing
    # API has no text-only post, every post needs media -- so this is
    # required for Instagram posts and optional for Telegram ones,
    # enforced by each platform's publisher, not at the schema level.
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Set instead of image_url for a generated video post (CIN-93) --
    # a Post carries at most one of the two, enforced by callers/
    # publishers, not at the schema level (same as image_url).
    video_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # "post" / "story" / etc -- same loose string as GenerationRequest.
    # content_kind (see schemas.py), copied over at Post creation so
    # publishers (e.g. instagram.py, CIN-74) know whether to publish a
    # Story instead of a regular feed post.
    content_kind: Mapped[str] = mapped_column(String(50), default="post", nullable=False)
    # Per-platform settings that must be chosen at review time instead
    # of silently defaulted by a publisher. TikTok in particular
    # requires the creator's current privacy options and interaction /
    # commercial-content disclosures to be shown before Direct Post.
    platform_options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, name="post_status"), default=PostStatus.scheduled, nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    platform_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", "external_account_id", name="uq_social_account"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        Enum(SocialPlatform, name="social_platform"), nullable=False
    )
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fernet-encrypted at rest -- see app/token_crypto.py. Never stored/returned in plaintext.
    encrypted_access_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class VideoProject(Base):
    """A video-studio project (CIN-135): script -> style -> brief ->
    finished video. Unlike a one-shot GenerationJob this is long-lived
    state the user returns to across days (shoot the footage, come
    back, download the brief again), so it's a first-class table.
    Status is derived from which fields are filled -- see the router's
    _project_status -- rather than stored, so it can never contradict
    the data."""

    __tablename__ = "video_projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(5000), nullable=False)
    brand_guide: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    script: Mapped[str | None] = mapped_column(String(20000), nullable=True)
    # A key from video_styles.VIDEO_STYLES -- validated at the API
    # boundary, a loose string here so adding styles needs no migration.
    style: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # list of {"filename", "title", "content"} -- the generated brief,
    # rendered in-app and downloadable as separate .md files.
    brief_files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # The finished video: either uploaded by the user (edited outside
    # the app from the brief) or produced by the veo_auto style via the
    # linked generation job. video_url wins when both exist.
    video_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    video_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # CIN-137: image GenerationJob ids (as strings) for the
    # auto-generated illustrations of blocks/cartoon briefs, in prompt
    # order. A JSONB list rather than a link table -- capped at 10,
    # only ever read back with the project.
    illustration_job_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
