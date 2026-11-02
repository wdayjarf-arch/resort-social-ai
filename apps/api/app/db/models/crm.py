"""Tenant-scoped CRM, conversation intelligence and campaign review models."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class CustomerLead(Base):
    __tablename__ = "customer_leads"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_customer_leads_org_id"),
        UniqueConstraint("organization_id", "source_channel", "external_contact_id", name="uq_customer_lead_external"),
        Index("ix_customer_leads_org_updated", "organization_id", "updated_at"),
        Index("ix_customer_leads_org_status", "organization_id", "lead_status"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    source_channel: Mapped[str] = mapped_column(String(30), nullable=False, default="messenger")
    external_contact_id: Mapped[str | None] = mapped_column(String(255))
    resort_interests: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    booking_history: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_summary: Mapped[str | None] = mapped_column(Text)
    guest_tone: Mapped[str | None] = mapped_column(String(120))
    preferred_services: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sentiment_score: Mapped[int | None] = mapped_column(Integer)
    lead_status: Mapped[str] = mapped_column(String(20), nullable=False, default="warm")
    engagement_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    consent_source: Mapped[str | None] = mapped_column(String(100))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    google_resource_name: Mapped[str | None] = mapped_column(String(255))
    google_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Campaign(Base):
    __tablename__ = "crm_campaigns"
    __table_args__ = (ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"), Index("ix_crm_campaigns_org_status", "organization_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    segment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft") # draft, pending_approval, approved, sending, completed, cancelled
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class CampaignRecipient(Base):
    __tablename__ = "crm_campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_recipient"), Index("ix_campaign_recipient_status", "campaign_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    personalized_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_review") # pending_review, queued, sent, failed, suppressed
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
