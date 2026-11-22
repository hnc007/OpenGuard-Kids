"""Expose the last actual device reading for the child's screen-time view."""
from alembic import op
import sqlalchemy as sa

revision = "0005_device_status"
down_revision = "0004_erasure_key"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("last_status", sa.String()))
    op.add_column("devices", sa.Column("remaining_seconds", sa.Integer()))


def downgrade():
    op.drop_column("devices", "remaining_seconds")
    op.drop_column("devices", "last_status")
