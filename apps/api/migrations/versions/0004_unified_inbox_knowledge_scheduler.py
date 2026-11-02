"""Unified inbox, resort knowledge, and scheduled publishing.

Revision ID: 0004_unified_ops
Revises: 0003_meta_provider
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.db.models.knowledge import Vector

revision = "0004_unified_ops"
down_revision = "0003_meta_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    uuid = postgresql.UUID(as_uuid=True)
    json_type = sa.JSON()

    op.create_table(
        "conversations",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("social_account_id", uuid, nullable=False), sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False), sa.Column("external_thread_id", sa.String(255), nullable=False),
        sa.Column("participant_id", sa.String(255), nullable=False), sa.Column("participant_name", sa.String(255)),
        sa.Column("status", sa.String(40), nullable=False, server_default="open"),
        sa.Column("assigned_to_user_id", uuid), sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_conversations_org_id"),
        sa.UniqueConstraint("social_account_id", "external_thread_id", name="uq_conversation_account_thread"),
    )
    op.create_index("ix_conversations_organization_id", "conversations", ["organization_id"])
    op.create_index("ix_conversations_social_account_id", "conversations", ["social_account_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_index("ix_conversations_org_status_updated", "conversations", ["organization_id", "status", "last_message_at"])

    op.create_table(
        "messages",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("conversation_id", uuid, nullable=False), sa.Column("social_account_id", uuid, nullable=False),
        sa.Column("provider_message_id", sa.String(255)), sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("sender_external_id", sa.String(255)), sa.Column("recipient_external_id", sa.String(255)),
        sa.Column("text", sa.Text()), sa.Column("attachments", json_type, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(40), nullable=False, server_default="received"),
        sa.Column("reply_to_message_id", uuid), sa.Column("intent", sa.String(40)), sa.Column("ai_confidence", sa.Float()),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("human_approved_by_user_id", uuid), sa.Column("human_approved_at", sa.DateTime(timezone=True)),
        sa.Column("provider_error_code", sa.String(80)), sa.Column("provider_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True)), sa.Column("delivered_at", sa.DateTime(timezone=True)), sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["organization_id", "conversation_id"], ["conversations.organization_id", "conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_messages_org_id"),
        sa.UniqueConstraint("social_account_id", "provider_message_id", name="uq_message_provider_id"),
        sa.UniqueConstraint("reply_to_message_id", name="uq_message_reply_to_message"),
    )
    for col in ("organization_id", "conversation_id", "social_account_id", "status"):
        op.create_index(f"ix_messages_{col}", "messages", [col])
    op.create_index("ix_messages_org_conversation_created", "messages", ["organization_id", "conversation_id", "created_at"])
    op.create_index("ix_messages_org_status_created", "messages", ["organization_id", "status", "created_at"])

    op.create_table(
        "post_engagements",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("social_account_id", uuid, nullable=False), sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False), sa.Column("provider_comment_id", sa.String(255), nullable=False),
        sa.Column("parent_post_id", sa.String(255)), sa.Column("parent_comment_id", sa.String(255)),
        sa.Column("author_external_id", sa.String(255)), sa.Column("author_name", sa.String(255)), sa.Column("text", sa.Text()),
        sa.Column("status", sa.String(40), nullable=False, server_default="received"), sa.Column("drafted_reply", sa.Text()),
        sa.Column("provider_reply_id", sa.String(255)), sa.Column("intent", sa.String(40)), sa.Column("ai_confidence", sa.Float()),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default=sa.text("false")), sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("replied_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("social_account_id", "provider_comment_id", name="uq_engagement_provider_comment"),
    )
    for col in ("organization_id", "social_account_id", "status"):
        op.create_index(f"ix_post_engagements_{col}", "post_engagements", [col])
    op.create_index("ix_engagements_org_status_created", "post_engagements", ["organization_id", "status", "created_at"])
    op.create_index("ix_engagements_org_parent_post", "post_engagements", ["organization_id", "parent_post_id"])

    op.create_table(
        "scheduled_posts",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("created_by_user_id", uuid), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("media_urls", json_type, nullable=False, server_default="[]"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="scheduled"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_error", sa.Text()),
        sa.Column("published_results", json_type, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_scheduled_posts_org_id"),
    )
    for col in ("organization_id", "scheduled_at", "status"):
        op.create_index(f"ix_scheduled_posts_{col}", "scheduled_posts", [col])
    op.create_index("ix_scheduled_posts_due", "scheduled_posts", ["status", "scheduled_at"])
    op.create_index("ix_scheduled_posts_org_status", "scheduled_posts", ["organization_id", "status", "scheduled_at"])

    op.create_table(
        "scheduled_post_targets",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("scheduled_post_id", uuid, nullable=False), sa.Column("social_account_id", uuid, nullable=False),
        sa.Column("published_id", sa.String(255)), sa.Column("status", sa.String(40), nullable=False, server_default="scheduled"),
        sa.Column("last_error", sa.Text()),
        sa.ForeignKeyConstraint(["organization_id", "scheduled_post_id"], ["scheduled_posts.organization_id", "scheduled_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id", "social_account_id"], ["social_accounts.organization_id", "social_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scheduled_post_id", "social_account_id", name="uq_schedule_target_account"),
    )
    for col in ("organization_id", "scheduled_post_id", "social_account_id"):
        op.create_index(f"ix_scheduled_post_targets_{col}", "scheduled_post_targets", [col])

    op.create_table(
        "resort_knowledge_items",
        sa.Column("id", uuid, primary_key=True), sa.Column("organization_id", uuid, nullable=False),
        sa.Column("property_id", uuid), sa.Column("category", sa.String(60), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(1000)), sa.Column("metadata", json_type, nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(1536)), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["properties.organization_id", "properties.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "id", name="uq_knowledge_org_id"),
    )
    for col in ("organization_id", "property_id", "category"):
        op.create_index(f"ix_resort_knowledge_items_{col}", "resort_knowledge_items", [col])
    op.create_index("ix_knowledge_org_category_active", "resort_knowledge_items", ["organization_id", "category", "is_active"])
    op.execute("CREATE INDEX ix_knowledge_embedding_cosine ON resort_knowledge_items USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_embedding_cosine")
    for table in ["resort_knowledge_items", "scheduled_post_targets", "scheduled_posts", "post_engagements", "messages", "conversations"]:
        op.drop_table(table)
