"""Let a device acknowledge local deletion across refresh rotation and disconnection."""
from alembic import op
import sqlalchemy as sa

revision = "0004_erasure_key"
down_revision = "0003_family"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("erasure_hash", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("local_wiped_at", sa.Integer(), nullable=True))
    # An upgrading agent preserves its last refresh as the legacy erasure proof.
    op.execute("UPDATE devices SET erasure_hash = refresh_hash")
    op.alter_column("erasures", "refresh_hash", new_column_name="proof_hash")


def downgrade():
    op.alter_column("erasures", "proof_hash", new_column_name="refresh_hash")
    op.drop_column("devices", "local_wiped_at")
    op.drop_column("devices", "erasure_hash")
