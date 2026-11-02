"""Tenant-scoped unified inbox, engagements, and scheduled publishing models."""
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON,
    String, Text, UniqueConstraint, func, text as sql_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ConversationStatus(str, enum.Enum):
    open = "open"
    pending = "pending"
    resolved = "resolved"
    archived = "archived"


class MessageDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class MessageStatus(str, enum.Enum):
    received = "received"
    drafted = "drafted"
    needs_human_approval = "needs_human_approval"
    approved = "approved"
    sending = "sending"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


class EngagementStatus(str, enum.Enum):
    received = "received"
    drafted = "drafted"
    needs_human_approval = "needs_human_approval"
    approved = "approved"
    replied = "replied"
    ignored = "ignored"
    failed = "failed"


class ScheduledPostStatus(str, enum.Enum):
    scheduled = "scheduled"
    publishing = "publishing"
    published = "published"
    partially_published = "partially_published"
    failed = "failed"
    cancelled = "cancelled"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_conversations_org_id"),
        UniqueConstraint("social_account_id", "external_thread_id", name="uq_conversation_account_thread"),
        ForeignKeyConstraint(["organization_id", "social_account_id"],
                             ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        Index("ix_conversations_org_status_updated", "organization_id", "status", "last_message_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="meta")
    channel: Mapped[str] = mapped_column(String(40), nullable=False)  # messenger | instagram
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default=ConversationStatus.open.value, index=True)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sql_text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_messages_org_id"),
        UniqueConstraint("social_account_id", "provider_message_id", name="uq_message_provider_id"),
        UniqueConstraint("reply_to_message_id", name="uq_message_reply_to_message"),
        ForeignKeyConstraint(["organization_id", "conversation_id"], ["conversations.organization_id", "conversations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        Index("ix_messages_org_conversation_created", "organization_id", "conversation_id", "created_at"),
        Index("ix_messages_org_status_created", "organization_id", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    sender_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default=MessageStatus.received.value, index=True)
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_text("false"))
    human_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="messages", foreign_keys=[organization_id, conversation_id])


class PostEngagement(Base):
    __tablename__ = "post_engagements"
    __table_args__ = (
        UniqueConstraint("social_account_id", "provider_comment_id", name="uq_engagement_provider_comment"),
        ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        Index("ix_engagements_org_status_created", "organization_id", "status", "created_at"),
        Index("ix_engagements_org_parent_post", "organization_id", "parent_post_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="meta")
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_comment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default=EngagementStatus.received.value, index=True)
    drafted_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_reply_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql_text("false"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_scheduled_posts_org_id"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        Index("ix_scheduled_posts_due", "status", "scheduled_at"),
        Index("ix_scheduled_posts_org_status", "organization_id", "status", "scheduled_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    media_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default=ScheduledPostStatus.scheduled.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sql_text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    targets: Mapped[list["ScheduledPostTarget"]] = relationship(back_populates="scheduled_post", cascade="all, delete-orphan")


class ScheduledPostTarget(Base):
    __tablename__ = "scheduled_post_targets"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "scheduled_post_id"], ["scheduled_posts.organization_id", "scheduled_posts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        UniqueConstraint("scheduled_post_id", "social_account_id", name="uq_schedule_target_account"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scheduled_post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    published_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="scheduled")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_post: Mapped[ScheduledPost] = relationship(back_populates="targets")
