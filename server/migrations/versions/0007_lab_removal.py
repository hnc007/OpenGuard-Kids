"""Keep the authorized and locally reported service-removal stages idempotent."""
from alembic import op
import sqlalchemy as sa

revision = "0007_lab_removal"
down_revision = "0006_public_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("lab_removals",
        sa.Column("device_id", sa.String, sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("authorized_at", sa.Integer, nullable=False),
        sa.Column("reported_at", sa.Integer))


def downgrade():
    op.drop_table("lab_removals")
