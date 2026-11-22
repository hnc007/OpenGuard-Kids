"""Paired devices, consent, versioned policies and minimal event envelopes."""

from alembic import op
import sqlalchemy as sa

revision = "0002_sync"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def child_ref(primary_key=False):
    return sa.Column("child_id", sa.Integer(), sa.ForeignKey("children.id", ondelete="CASCADE"),
                     primary_key=primary_key, nullable=False)


def field(name, kind):
    return sa.Column(name, kind, nullable=False)


def upgrade():
    op.create_table("devices", sa.Column("id", sa.String(), primary_key=True), child_ref(),
                    field("label", sa.String()), sa.Column("fingerprint_hash", sa.String(), unique=True, nullable=False),
                    field("access_hash", sa.String()), field("access_until", sa.Integer()),
                    field("refresh_hash", sa.String()), field("refresh_until", sa.Integer()),
                    field("revoked", sa.Boolean()), sa.Column("last_seen", sa.Integer()),
                    field("used_seconds", sa.Integer()), field("policy_version", sa.Integer()),
                    field("consent_at", sa.Integer()), field("consent_parent_id", sa.Integer()),
                    field("consent_scope", sa.String()))
    op.create_index("ix_devices_child_id", "devices", ["child_id"])
    op.create_table("pair_codes", sa.Column("digest", sa.String(), primary_key=True), child_ref(),
                    field("parent_id", sa.Integer()), field("created_at", sa.Integer()), field("expires_at", sa.Integer()))
    op.create_index("ix_pair_codes_child_id", "pair_codes", ["child_id"])
    op.create_table("pair_attempts", sa.Column("source_hash", sa.String(), primary_key=True),
                    field("started", sa.Integer()), field("count", sa.Integer()))
    op.create_table("policies", child_ref(primary_key=True), field("version", sa.Integer()), field("body", sa.Text()))
    op.create_table("usage_events", sa.Column("id", sa.Integer(), primary_key=True), child_ref(),
                    sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
                    field("event_key", sa.String()), field("ts", sa.Integer()), field("type", sa.String()),
                    field("subject", sa.String()), field("duration_sec", sa.Integer()), field("policy_id", sa.Integer()),
                    sa.UniqueConstraint("device_id", "event_key", name="uq_device_event"))
    op.create_index("ix_usage_events_child_id", "usage_events", ["child_id"])
    op.create_index("ix_usage_events_ts", "usage_events", ["ts"])


def downgrade():
    for table in ("usage_events", "policies", "pair_attempts", "pair_codes", "devices"):
        op.drop_table(table)
