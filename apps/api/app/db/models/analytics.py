"""Durable Meta Insights snapshots for tenant-scoped analytics and peak-time scoring."""
import uuid
from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        UniqueConstraint("social_account_id", "period_start", "metric_source", name="uq_analytics_account_period_source"),
        Index("ix_analytics_org_period", "organization_id", "period_start"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_source: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reach: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
