"""Track last inbound customer message separately for messaging-window enforcement."""
from alembic import op
import sqlalchemy as sa
revision = "0007_campaign_delivery"
down_revision = "0006_resort_crm"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("customer_leads", sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True))
def downgrade():
    op.drop_column("customer_leads", "last_inbound_at")
