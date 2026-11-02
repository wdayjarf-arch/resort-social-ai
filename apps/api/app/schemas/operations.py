from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from typing import Annotated

TextBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    direction: str
    text: str | None
    status: str
    intent: str | None = None
    ai_confidence: float | None = None
    ai_generated: bool
    created_at: datetime
    sent_at: datetime | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    social_account_id: UUID
    channel: str
    participant_id: str
    participant_name: str | None
    status: str
    unread_count: int
    last_message_at: datetime | None


class KnowledgeCreate(BaseModel):
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    property_id: UUID | None = None
    source_url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    embed_now: bool = True


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    property_id: UUID | None
    category: str
    title: str
    content: str
    source_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    top_k: int = Field(default=5, ge=1, le=10)
    property_id: UUID | None = None


class ScheduledPostCreate(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2200)]
    media_urls: list[HttpUrl] = Field(default_factory=list, max_length=1)
    target_account_ids: list[UUID] = Field(min_length=1, max_length=20)
    scheduled_at: datetime


class ScheduledPostOut(BaseModel):
    id: UUID
    content: str
    media_urls: list[str]
    target_account_ids: list[UUID]
    scheduled_at: datetime
    status: str
    attempts: int
    last_error: str | None
    published_results: dict[str, Any]
    created_at: datetime


class ApproveReplyRequest(BaseModel):
    edited_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)] | None = None


class EngagementReplyRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    send_now: bool = True
