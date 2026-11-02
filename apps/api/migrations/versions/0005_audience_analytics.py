"""Audience analytics snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0005_audience_analytics"
down_revision = "0004_unified_ops"
branch_labels = None
depends_on = None

def upgrade():
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table("analytics_snapshots",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("social_account_id", uuid, nullable=False), sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("metric_source", sa.String(100), nullable=False), sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True)), sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"), sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"), sa.Column("engagements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("social_account_id", "period_start", "metric_source", name="uq_analytics_account_period_source"))
    op.create_index("ix_analytics_snapshots_organization_id", "analytics_snapshots", ["organization_id"])
    op.create_index("ix_analytics_snapshots_social_account_id", "analytics_snapshots", ["social_account_id"])
    op.create_index("ix_analytics_org_period", "analytics_snapshots", ["organization_id", "period_start"])

def downgrade():
    op.drop_table("analytics_snapshots")
