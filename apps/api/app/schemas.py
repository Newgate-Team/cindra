import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.content_pipeline.attachments import (
    TooManyAttachmentsError,
    validate_attachment_set,
)
from app.content_pipeline.prompts import TONE_GUIDANCE
from app.image_templates import IMAGE_TEMPLATES
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


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=1)


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
    # CIN-138: optional tone preset -- a key of prompts.TONE_GUIDANCE.
    # None = the model's default voice.
    tone: str | None = None
    # CIN-143: optional image template -- a key of IMAGE_TEMPLATES.
    # Only meaningful for content_type=image (other generators ignore
    # it); None = free-form, CIN-142's enhancer works from the topic
    # alone.
    image_template: str | None = None
    # Optional context files (CIN-97, extended to a list in CIN-107) --
    # set by the client after successful POST /content/attachment
    # upload(s), one call per file. Up to 5 total, video/audio capped
    # at 1 each -- see content_pipeline/attachments.py.validate_attachment_set.
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=5)

    @field_validator("tone")
    @classmethod
    def _validate_tone(cls, value: str | None) -> str | None:
        if value is not None and value not in TONE_GUIDANCE:
            raise ValueError(
                f"Неизвестный тон: {value}. Доступные: {', '.join(sorted(TONE_GUIDANCE))}"
            )
        return value

    @field_validator("image_template")
    @classmethod
    def _validate_image_template(cls, value: str | None) -> str | None:
        if value is not None and value not in IMAGE_TEMPLATES:
            raise ValueError(
                f"Неизвестный шаблон: {value}. Доступные: {', '.join(sorted(IMAGE_TEMPLATES))}"
            )
        return value

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
    `caption` is the generated caption (CIN-114's output_payload["text"],
    the same text a user would publish alongside the media -- not a
    privacy concern beyond publishing itself), falling back to the raw
    prompt (input_payload["topic"]) when a caption wasn't generated
    (CIN-114 is best-effort). Deliberately NOT output_payload["prompt"]:
    image_generator.py folds brand_guide straight into that string, so
    showing it would leak another user's brand-guide text indirectly."""

    id: uuid.UUID
    content_type: GenerationContentType
    image_url: str | None
    video_url: str | None
    caption: str
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
    platform_options: dict[str, Any] = Field(default_factory=dict)


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


def _normalize_chat_id(value: str) -> str:
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


class TelegramStartVerificationRequest(BaseModel):
    chat_id: str = Field(
        min_length=1,
        description="Telegram @username, username, t.me-ссылка на публичный канал или numeric chat_id",
    )

    @field_validator("chat_id")
    @classmethod
    def normalize_chat_id(cls, value: str) -> str:
        return _normalize_chat_id(value)


class TelegramStartVerificationOut(BaseModel):
    """CIN-128: proof that whoever finishes the connect flow actually
    controls the channel -- editing a channel's description requires
    Telegram's own "Change Channel Info" admin permission, so
    successfully placing `code` there and having us see it back is
    itself the ownership proof. `verification_token` is a short-lived
    signed token (see security.py) binding this code to this chat_id
    so /telegram/connect can't be handed an unrelated code/chat pair."""

    code: str
    verification_token: str
    chat_title: str | None


class TelegramConnectRequest(BaseModel):
    verification_token: str = Field(min_length=1)


class InstagramConnectRequest(BaseModel):
    code: str = Field(
        min_length=1, description="Authorization code от Meta OAuth-редиректа (см. CIN-52)"
    )


class TikTokOAuthStartOut(BaseModel):
    authorization_url: str


class TikTokConnectRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class TikTokCreatorInfoOut(BaseModel):
    creator_username: str
    creator_nickname: str
    creator_avatar_url: str | None = None
    privacy_level_options: list[str]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int


class TikTokPublishStatusOut(BaseModel):
    status: str
    fail_reason: str | None = None
    publicly_available_post_id: list[str] = Field(default_factory=list)


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


class VideoProjectCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=5000)
    brand_guide: str | None = Field(default=None, max_length=5000)


class VideoProjectUpdate(BaseModel):
    """Edit a studio project (CIN-135): all fields optional, only what's
    passed changes (exclude_unset in the router). `script` covers the
    review-and-edit step after generation; `style` is validated against
    the VIDEO_STYLES catalog in the router."""

    topic: str | None = Field(default=None, min_length=1, max_length=5000)
    brand_guide: str | None = Field(default=None, max_length=5000)
    script: str | None = Field(default=None, min_length=1, max_length=20000)
    style: str | None = None


class BriefFileOut(BaseModel):
    filename: str
    title: str
    content: str


class IllustrationOut(BaseModel):
    """One auto-generated brief illustration (CIN-137) -- mirrors the
    linked image GenerationJob, resolved by the router."""

    prompt: str
    status: GenerationStatus
    image_url: str | None
    error_message: str | None


class VideoProjectOut(BaseModel):
    id: uuid.UUID
    topic: str
    brand_guide: str | None
    script: str | None
    style: str | None
    brief_files: list[BriefFileOut] | None
    # The finished video: uploaded file or completed veo_auto job
    # output -- resolved by the router, which also surfaces the linked
    # job's in-flight status/error here so the wizard needs no second
    # polling endpoint.
    video_url: str | None
    video_status: GenerationStatus | None
    video_error: str | None
    # CIN-137: auto-generated illustrations for blocks/cartoon briefs
    # (None when never requested for this project).
    illustrations: list[IllustrationOut] | None
    # draft -> script_ready -> brief_ready -> video_ready, derived
    # from field presence (see router _project_status).
    status: str
    created_at: datetime
    updated_at: datetime


class LayoutSlotOut(BaseModel):
    name: str
    label: str
    max_length: int
    required: bool


class LayoutTemplateOut(BaseModel):
    # CIN-148: the `blocks` render spec stays server-side -- the UI only
    # needs to know what to ask the user for and how to preview it.
    id: str
    title: str
    description: str
    supports_image: bool
    slots: list[LayoutSlotOut]


class LayoutRenderRequest(BaseModel):
    template_id: str
    canvas_format: str = "square"
    theme: str = "dark"
    # Optional brand accent, "#RRGGBB" -- overrides the theme's accent.
    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    values: dict[str, str] = Field(default_factory=dict)
    # Only for templates with supports_image, and only a URL from our
    # own media bucket (validated in the renderer).
    background_url: str | None = None

    @field_validator("values")
    @classmethod
    def _cap_value_sizes(cls, value: dict[str, str]) -> dict[str, str]:
        # A cheap guard before the renderer's per-slot max_length: keeps
        # a giant payload from being wrapped and measured at all.
        if any(len(v) > 1000 for v in value.values()):
            raise ValueError("Слишком длинное значение поля")
        return value


class LayoutRenderOut(BaseModel):
    image_url: str


class ImageTemplateOut(BaseModel):
    # CIN-143: the English `directive` is deliberately not exposed --
    # it's prompt internals, the UI only needs id/title/description.
    id: str
    title: str
    description: str
    # CIN-150: a stored example, or None until staff have generated one
    # (it costs a real image generation, unlike the layout previews).
    preview_url: str | None = None


class ImageTemplatePreviewsOut(BaseModel):
    generated: list[str]
    failed: dict[str, str]


class VideoStyleOut(BaseModel):
    id: str
    title: str
    description: str
    # "brief": produces a production brief to shoot from;
    # "clip": generates the finished clip itself (veo_auto).
    produces: str
    # CIN-137: the studio can generate this style's visuals itself.
    generates_illustrations: bool = False
