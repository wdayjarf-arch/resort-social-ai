"""Resort CRM, contact intelligence and reviewed campaigns."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0006_resort_crm"
down_revision = "0005_audience_analytics"
branch_labels = None
depends_on = None

def upgrade():
    u = postgresql.UUID(as_uuid=True)
    op.create_table("customer_leads",
        sa.Column("id", u, primary_key=True), sa.Column("organization_id", u, nullable=False),
        sa.Column("full_name", sa.String(200)), sa.Column("phone_e164", sa.String(20)), sa.Column("email", sa.String(320)),
        sa.Column("source_channel", sa.String(30), nullable=False, server_default="messenger"), sa.Column("external_contact_id", sa.String(255)),
        sa.Column("resort_interests", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("booking_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True)), sa.Column("transcript_summary", sa.Text()),
        sa.Column("guest_tone", sa.String(120)), sa.Column("preferred_services", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sentiment_score", sa.Integer()), sa.Column("lead_status", sa.String(20), nullable=False, server_default="warm"),
        sa.Column("engagement_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consent_source", sa.String(100)), sa.Column("consent_at", sa.DateTime(timezone=True)),
        sa.Column("google_resource_name", sa.String(255)), sa.Column("google_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "id", name="uq_customer_leads_org_id"),
        sa.UniqueConstraint("organization_id", "source_channel", "external_contact_id", name="uq_customer_lead_external"))
    op.create_index("ix_customer_leads_organization_id", "customer_leads", ["organization_id"])
    op.create_index("ix_customer_leads_phone_e164", "customer_leads", ["phone_e164"])
    op.create_index("ix_customer_leads_email", "customer_leads", ["email"])
    op.create_index("ix_customer_leads_org_updated", "customer_leads", ["organization_id", "updated_at"])
    op.create_index("ix_customer_leads_org_status", "customer_leads", ["organization_id", "lead_status"])
    op.create_table("crm_campaigns",
        sa.Column("id", u, primary_key=True), sa.Column("organization_id", u, nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False), sa.Column("segment", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("template", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("created_by_user_id", u), sa.Column("approved_by_user_id", u), sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"))
    op.create_index("ix_crm_campaigns_organization_id", "crm_campaigns", ["organization_id"])
    op.create_index("ix_crm_campaigns_org_status", "crm_campaigns", ["organization_id", "status"])
    op.create_table("crm_campaign_recipients",
        sa.Column("id", u, primary_key=True), sa.Column("campaign_id", u, nullable=False), sa.Column("lead_id", u, nullable=False),
        sa.Column("personalized_message", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False, server_default="pending_review"),
        sa.Column("provider_message_id", sa.String(255)), sa.Column("error_code", sa.String(100)), sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["campaign_id"], ["crm_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["customer_leads.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_recipient"))
    op.create_index("ix_crm_campaign_recipients_campaign_id", "crm_campaign_recipients", ["campaign_id"])
    op.create_index("ix_crm_campaign_recipients_lead_id", "crm_campaign_recipients", ["lead_id"])
    op.create_index("ix_campaign_recipient_status", "crm_campaign_recipients", ["campaign_id", "status"])

def downgrade():
    op.drop_table("crm_campaign_recipients"); op.drop_table("crm_campaigns"); op.drop_table("customer_leads")
