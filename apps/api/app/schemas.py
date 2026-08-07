import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.content_pipeline.attachments import (
    TooManyAttachmentsError,
    validate_attachment_set,
)
from app.models import (
    GenerationContentType,
    GenerationStatus,
    PostStatus,
    SocialPlatform,
    SubscriptionStatus,
    SubscriptionTier,
    UserRole,
)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: UserRole = UserRole.solo


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    role: UserRole


class SubscriptionOut(BaseModel):
    tier: SubscriptionTier
    status: SubscriptionStatus
    current_period_end: datetime | None

    model_config = {"from_attributes": True}


class AttachmentRef(BaseModel):
    url: str
    attachment_type: str


class GenerationRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=5000)
    # Target accounts are chosen up front (CIN-106), before generation --
    # content_type/content_kind are validated against the intersection of
    # what all of them can actually publish (see publish_matrix.py), so
    # e.g. an Instagram target rules out content_type=text before
    # anything gets generated, not after, at publish time.
    target_account_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    content_type: GenerationContentType = GenerationContentType.text
    content_kind: str = "post"
    brand_guide: str | None = None
    # Optional context files (CIN-97, extended to a list in CIN-107) --
    # set by the client after successful POST /content/attachment
    # upload(s), one call per file. Up to 5 total, video/audio capped
    # at 1 each -- see content_pipeline/attachments.py.validate_attachment_set.
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _validate_attachment_set(self) -> "GenerationRequest":
        try:
            validate_attachment_set([a.attachment_type for a in self.attachments])
        except TooManyAttachmentsError as exc:
            raise ValueError(str(exc)) from exc
        return self


class AttachmentOut(BaseModel):
    url: str
    attachment_type: str
    mime_type: str


class GenerationJobOut(BaseModel):
    id: uuid.UUID
    content_type: GenerationContentType
    status: GenerationStatus
    output_payload: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class FeedItemOut(BaseModel):
    """A shared, cross-user feed item (CIN-109) -- deliberately excludes
    the generating user's identity, error_message, and brand_guide.
    `topic` is the user's own prompt (input_payload), not
    output_payload["prompt"]: image_generator.py folds brand_guide
    straight into the stored prompt string, so showing that would leak
    another user's brand-guide text indirectly."""

    id: uuid.UUID
    content_type: GenerationContentType
    image_url: str | None
    video_url: str | None
    topic: str
    created_at: datetime


class PostCreate(BaseModel):
    # Fan-out publish (CIN-106): one generated piece of content can go
    # to several accounts/platforms at once -- one Post row gets
    # created per id, all sharing generation_job_id.
    social_account_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    text: str = Field(min_length=1, max_length=4096)
    image_url: str | None = None  # required for Instagram, optional for Telegram
    video_url: str | None = None  # mutually exclusive with image_url -- see Post.video_url
    scheduled_for: datetime | None = None  # None = publish as soon as possible
    generation_job_id: uuid.UUID | None = None
    content_kind: str = "post"  # "post" / "story" -- see Post.content_kind (CIN-74)


class PostUpdate(BaseModel):
    """Перенос/редактирование запланированной публикации (CIN-77).
    Оба поля необязательны -- передаётся только то, что меняется
    (exclude_unset в роутере), не указанные остаются как есть."""

    text: str | None = Field(default=None, min_length=1, max_length=4096)
    scheduled_for: datetime | None = None


class PostOut(BaseModel):
    id: uuid.UUID
    social_account_id: uuid.UUID
    text: str
    image_url: str | None
    video_url: str | None
    content_kind: str
    status: PostStatus
    scheduled_for: datetime
    platform_message_id: str | None
    error_message: str | None
    created_at: datetime
    published_at: datetime | None
    # Denormalized from the related SocialAccount (CIN-83) -- the
    # calendar list needs to show where a post goes without a second
    # round-trip to /social-accounts. account_label is display_name
    # with a fallback to external_account_id, computed by the router
    # (not present on the Post ORM model itself).
    platform: SocialPlatform
    account_label: str

    model_config = {"from_attributes": True}


_TME_LINK_RE = re.compile(r"^(?:https?://)?t\.me/(.+)$", re.IGNORECASE)


class TelegramConnectRequest(BaseModel):
    chat_id: str = Field(
        min_length=1,
        description="Telegram @username, username, t.me-ссылка на публичный канал или numeric chat_id",
    )

    @field_validator("chat_id")
    @classmethod
    def normalize_chat_id(cls, value: str) -> str:
        value = value.strip()

        match = _TME_LINK_RE.match(value)
        if match:
            path = match.group(1).strip("/")
            if path.startswith(("+", "joinchat/")):
                raise ValueError(
                    "Приватные инвайт-ссылки (t.me/+... или t.me/joinchat/...) не поддерживаются -- "
                    "укажите @username канала/группы или его numeric chat_id"
                )
            username = path.split("/")[0].split("?")[0]
            return f"@{username}"

        if value.startswith("@") or re.fullmatch(r"-?\d+", value):
            return value

        return f"@{value}"


class InstagramConnectRequest(BaseModel):
    code: str = Field(
        min_length=1, description="Authorization code от Meta OAuth-редиректа (см. CIN-52)"
    )


class SocialAccountOut(BaseModel):
    id: uuid.UUID
    platform: SocialPlatform
    external_account_id: str
    display_name: str | None
    token_expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfirmSubscriptionRequest(BaseModel):
    """Sent by the frontend after the PayPal Buttons SDK's onApprove
    fires (see CIN-87) -- just the subscription id, everything else
    (status, plan, owner) is looked up server-side from PayPal itself
    rather than trusted from the client."""

    subscription_id: str = Field(min_length=1)


class MetricsSummary(BaseModel):
    average_time_to_first_post_seconds: float | None
    retention_d7: float
    retention_d30: float
    publish_success_rate: float | None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
