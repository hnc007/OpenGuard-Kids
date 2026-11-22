"""Child feedback, short-lived commands and offline removal receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0003_family"
down_revision = "0002_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("family_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("child_id", sa.Integer, sa.ForeignKey("children.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False), sa.Column("domain_cipher", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False), sa.Column("answer_cipher", sa.String, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False), sa.Column("resolved_at", sa.Integer))
    op.create_index("ix_family_requests_child_id", "family_requests", ["child_id"])
    op.create_table("commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String, nullable=False), sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False), sa.Column("acknowledged_at", sa.Integer))
    op.create_index("ix_commands_device_id", "commands", ["device_id"])
    op.create_table("erasures",
        sa.Column("device_id", sa.String, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_hash", sa.String, nullable=False), sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("acknowledged_at", sa.Integer))
    op.create_index("ix_erasures_parent_id", "erasures", ["parent_id"])


def downgrade():
    for table in ("erasures", "commands", "family_requests"):
        op.drop_table(table)
