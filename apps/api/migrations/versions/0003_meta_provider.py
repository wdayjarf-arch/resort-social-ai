"""Add Meta OAuth state and durable webhook inbox.

Revision ID: 0003_meta_provider
Revises: 0002_security_tenant_access
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_meta_provider"
down_revision = "0002_security_tenant_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("state_digest", name="uq_meta_oauth_states_digest"),
    )
    op.create_index("ix_meta_oauth_states_expires", "meta_oauth_states", ["expires_at"])
    op.create_index("ix_meta_oauth_states_organization_id", "meta_oauth_states", ["organization_id"])
    op.create_index("ix_meta_oauth_states_user_id", "meta_oauth_states", ["user_id"])

    op.create_table(
        "meta_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_object", sa.String(80), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'received'")),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("fingerprint", name="uq_meta_webhook_events_fingerprint"),
    )
    op.create_index("ix_meta_webhook_events_received", "meta_webhook_events", ["received_at"])
    op.create_index("ix_meta_webhook_events_organization_id", "meta_webhook_events", ["organization_id"])
    op.create_index("ix_meta_webhook_events_status", "meta_webhook_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_meta_webhook_events_status", table_name="meta_webhook_events")
    op.drop_index("ix_meta_webhook_events_organization_id", table_name="meta_webhook_events")
    op.drop_index("ix_meta_webhook_events_received", table_name="meta_webhook_events")
    op.drop_table("meta_webhook_events")
    op.drop_index("ix_meta_oauth_states_user_id", table_name="meta_oauth_states")
    op.drop_index("ix_meta_oauth_states_organization_id", table_name="meta_oauth_states")
    op.drop_index("ix_meta_oauth_states_expires", table_name="meta_oauth_states")
    op.drop_table("meta_oauth_states")
