"""Families, sessions, throttling and audit history."""
from alembic import op
import sqlalchemy as sa

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(180), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("child_id", sa.Integer, sa.ForeignKey("children.id", ondelete="CASCADE"), unique=True),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.Integer, nullable=False),
        sa.CheckConstraint("role IN ('parent','child-view','admin')"),
        sa.CheckConstraint("(role = 'child-view') = (child_id IS NOT NULL)"),
    )
    op.create_table("children",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name_cipher", sa.Text, nullable=False),
    )
    op.create_index("ix_children_parent_id", "children", ["parent_id"])
    op.create_table("sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table("login_windows",
        sa.Column("bucket", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
    )
    op.create_index("ix_login_windows_expires_at", "login_windows", ["expires_at"])
    op.create_table("audits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("child_id", sa.Integer, sa.ForeignKey("children.id", ondelete="CASCADE")),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ts", sa.Integer, nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("ip_cipher", sa.Text, nullable=False),
        sa.Column("before_cipher", sa.Text, nullable=False),
        sa.Column("after_cipher", sa.Text, nullable=False),
    )
    op.create_index("ix_audits_child_ts", "audits", ["child_id", "ts"])


def downgrade():
    for name in ("audits", "login_windows", "sessions", "children", "users"):
        op.drop_table(name)
