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
    youtube = "youtube"


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
    # CIN-148: a laid-out template render costs no AI money at all --
    # only CPU and a file in R2. Capped separately (generously) so it
    # can't be looped to fill the bucket, not because it's expensive.
    layout_render = "layout_render"


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
    # CIN-159: brute-force lockout on /auth/login. failed_login_attempts
    # resets to 0 on any successful login; locked_until is set once the
    # threshold is hit and is NOT extended by further attempts during
    # the lockout window (see app/security.py) -- otherwise an attacker
    # could keep a victim locked out indefinitely just by continuing to
    # guess.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # CIN-109's Лента is a shared feed across ALL users by design (confirmed
    # with the user at the time) -- but for an agency, that means an
    # unreleased client campaign's generated image/video is visible to every
    # other user (competitors included) before the client ever sees it
    # published. register() sets this False for role=agency, True for
    # role=solo -- preserving the original shared-feed experience for the
    # audience it was designed for, closing the gap for the one it wasn't.
    # Editable via PATCH /auth/me regardless of role, so either audience can
    # override the default in either direction.
    share_generations_to_feed: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    # False until the address is confirmed via a mailed link, or the
    # account came from Google (which already proved ownership, same
    # reasoning as CIN-140's password-drop on Google sign-in). Informational
    # only for now -- nothing in the product is gated on this yet.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # NULL = solo user, resources scoped to just this user_id (the
    # original, only behaviour before roadmap item 5). Set once this
    # user creates or accepts a team -- app/teams.py's
    # visible_user_ids() is the single place that turns this into "who
    # else can see this user's stuff", so every resource-ownership
    # query goes through that helper rather than re-deriving team
    # membership itself.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    @property
    def has_password(self) -> bool:
        """UserOut exposes this, never hashed_password itself -- a
        Google-only account (see hashed_password's own comment) has
        nothing for POST /auth/change-password to check against, so the
        frontend needs to know whether to offer that form at all."""
        return self.hashed_password is not None


class Team(Base):
    """Roadmap item 5 -- a shared agency workspace. Deliberately thin:
    creation is gated to role=agency (see POST /team), and everything
    a team actually shares (connected accounts, posts, generations,
    billing quota) is wired in as separate, incremental follow-ups via
    app/teams.py::visible_user_ids() rather than all at once here --
    each resource type gets its own dedicated cross-team isolation
    tests before its queries start trusting team_id.
    """

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The billing/management authority -- who can invite, remove
    # members, and (once wired in) whose Subscription the whole team
    # draws quota from. Not just "whoever created it" forever: nothing
    # currently supports transferring this, but the column exists
    # separately from "first member" so that's addable later without a
    # schema change.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TeamInvite(Base):
    """One outstanding invite to join a team, identified by email (the
    invitee doesn't need an account yet to be invited). Accepting is a
    separate, AUTHENTICATED action (POST /team/invites/accept) that
    requires the logged-in user's own email to match -- deliberately
    not "anyone who has the link joins automatically", and deliberately
    not auto-creating an account either: an existing user's own
    pre-team resources stay theirs until they explicitly accept, so a
    careless invite can't silently expose someone's private drafts to
    a team they never agreed to join.
    """

    __tablename__ = "team_invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PasswordResetToken(Base):
    """One row per outstanding "forgot password" request. Only a
    SHA-256 hash of the token is stored -- the raw, high-entropy token
    only ever exists in the emailed link, same reasoning as never
    storing a plaintext password (a DB leak alone shouldn't hand over
    reset capability for every account). used_at makes each row
    single-use; expires_at bounds how long a leaked/intercepted link
    stays dangerous.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class EmailVerificationToken(Base):
    """Same shape and reasoning as PasswordResetToken (hash-only
    storage, single-use, expiring) -- a separate table rather than a
    shared one because the two token kinds have different lifetimes
    (see EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES) and mixing them would
    make expiry logic depend on a "kind" column instead of the table
    itself.
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ImageTemplatePreview(Base):
    """One generated example per AI image template (CIN-150).

    Unlike the code-rendered layout templates (CIN-148), whose previews
    are drawn on the fly for free, an example for these costs a real
    image generation -- so it is produced once by staff and stored,
    not rendered per request. `template_id` is a key of
    IMAGE_TEMPLATES, deliberately a plain string so adding or renaming
    a template needs no migration.
    """

    __tablename__ = "image_template_previews"

    template_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    preview_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
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
    # Set when this job is one variant of an A/B test (see ABTest) --
    # NULL for every plain, standalone generation, which is most of
    # them. ondelete SET NULL (not CASCADE): deleting a test shouldn't
    # take a real, already-paid-for generation result down with it.
    ab_test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ab_tests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ABTest(Base):
    """A set of independently generated text variants for the same
    brief (CIN roadmap item 4) -- each variant is a completely ordinary
    GenerationJob (content_type=text), just tagged with this test's id
    (GenerationJob.ab_test_id). Reuses the exact same generation
    pipeline/Celery task as a standalone text generation; this table
    only owns the grouping and the (manually chosen) winner.

    There's no automated winner-picking: no platform integration in
    this codebase reads back engagement data (likes/views/comments) to
    compare variants against -- see app/analytics.py's own docstring
    reasoning. So the user reviews the generated variants themselves
    and marks one via POST /ab-tests/{id}/winner once they've decided
    (typically after publishing and checking the platform's own
    insights) -- winner_generation_job_id is purely a record of that
    choice, not a computed result.
    """

    __tablename__ = "ab_tests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(5000), nullable=False)
    winner_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        # CIN-122's idempotency check in routers/posts.py relies on
        # (generation_job_id, social_account_id) uniquely identifying
        # "this generated content, published to this account" -- but
        # only ever enforced it by reading first, racily, with no lock
        # or constraint backing it. Concurrent retries of the same
        # create_post request (the exact CIN-120 scenario CIN-122 was
        # written for) could both pass the check and both insert,
        # double-charging usage and double-publishing to the real
        # platform. Postgres treats NULL as distinct from NULL in a
        # UNIQUE constraint, so this only ever constrains generated
        # posts (generation_job_id IS NOT NULL) -- manually-composed
        # posts (NULL) are unaffected and can repeat freely.
        UniqueConstraint(
            "generation_job_id", "social_account_id", name="uq_post_generation_job_account"
        ),
    )

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
