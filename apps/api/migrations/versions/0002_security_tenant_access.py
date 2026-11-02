"""Add password hashes, tracked auth sessions, and organization invitations.

Revision ID: 0002_security_tenant_access
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_security_tenant_access"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum additions are additive and safe for existing rows.
    op.execute("ALTER TYPE membership_role ADD VALUE IF NOT EXISTS 'agent'")

    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("jti", name="uq_auth_sessions_jti"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_user_expires", "auth_sessions", ["user_id", "expires_at"])

    op.create_table(
        "organization_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.Enum(
            "owner", "admin", "manager", "publisher", "analyst", "support", "agent", "viewer",
            name="membership_role", create_type=False
        ), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_organization_invitations_token_hash"),
    )
    op.create_index("ix_organization_invitations_organization_id", "organization_invitations", ["organization_id"])
    op.create_index("ix_org_invitations_org_email", "organization_invitations", ["organization_id", "email"])
    op.create_index("ix_org_invitations_expires", "organization_invitations", ["expires_at"])


def downgrade() -> None:
    op.drop_table("organization_invitations")
    op.drop_table("auth_sessions")
    op.drop_column("users", "password_hash")
    # PostgreSQL enum values cannot be removed safely in-place; leave 'agent' in enum.
