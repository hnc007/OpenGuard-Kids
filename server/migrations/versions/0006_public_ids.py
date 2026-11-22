"""Keep stale parent tabs from targeting a different child or feedback request after deletion."""
from alembic import op
import sqlalchemy as sa

revision = "0006_public_ids"
down_revision = "0005_device_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("public_id_counters", sa.Column("resource", sa.String, primary_key=True),
                    sa.Column("last_id", sa.Integer, nullable=False))
    for table in ("children", "family_requests"):
        # Start above historical small rowids on upgrades, including rows deleted before this migration.
        op.execute(f"INSERT INTO public_id_counters SELECT '{table}', "
                   f"MAX(COALESCE(MAX(id), 0), 4294967296) FROM {table}")


def downgrade():
    op.drop_table("public_id_counters")
