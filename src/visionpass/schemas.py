from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from visionpass.models import AccessDecision, TemplateStatus, UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole


class EventCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def valid_interval(self) -> "EventCreate":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Event times must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime


class ParticipantCreate(BaseModel):
    external_id: str = Field(min_length=2, max_length=80)
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr


class ParticipantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    external_id: str
    full_name: str
    email: EmailStr
    deleted_at: datetime | None
    created_at: datetime


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    participant_id: UUID
    generation: int
    encoder_id: str | None
    image_expires_at: datetime | None
    expires_at: datetime | None
    status: TemplateStatus
    consent_at: datetime
    processed_at: datetime | None
    deleted_at: datetime | None
    error: str | None


class AccessAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    participant_id: UUID | None
    encoder_id: str | None
    template_generation: int | None
    decision: AccessDecision
    distance: float | None
    confidence: float | None
    reason: str
    created_at: datetime


class ReviewCreate(BaseModel):
    resolution: Literal["granted", "denied"]
    note: str = Field(min_length=3, max_length=1000)


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    attempt_id: UUID
    reviewer_id: UUID
    resolution: AccessDecision
    note: str
    created_at: datetime


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID
    details: dict[str, object]
    created_at: datetime
