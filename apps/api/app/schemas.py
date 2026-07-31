import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.models import (
    GenerationContentType,
    GenerationStatus,
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
