import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

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


class GenerationRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    platform: SocialPlatform
    content_type: GenerationContentType = GenerationContentType.text
    content_kind: str = "post"
    brand_guide: str | None = None


class GenerationJobOut(BaseModel):
    id: uuid.UUID
    content_type: GenerationContentType
    status: GenerationStatus
    output_payload: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class PostCreate(BaseModel):
    social_account_id: uuid.UUID
    text: str = Field(min_length=1, max_length=4096)
    image_url: str | None = None  # required for Instagram, optional for Telegram
    scheduled_for: datetime | None = None  # None = publish as soon as possible
    generation_job_id: uuid.UUID | None = None


class PostOut(BaseModel):
    id: uuid.UUID
    social_account_id: uuid.UUID
    text: str
    image_url: str | None
    status: PostStatus
    scheduled_for: datetime
    platform_message_id: str | None
    error_message: str | None
    created_at: datetime
    published_at: datetime | None

    model_config = {"from_attributes": True}


class TelegramConnectRequest(BaseModel):
    chat_id: str = Field(min_length=1, description="Telegram @username или numeric chat_id")


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


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
