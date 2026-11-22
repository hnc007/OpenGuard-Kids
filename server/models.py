from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, next_public_id


class PublicIdCounter(Base):
    __tablename__ = "public_id_counters"
    resource: Mapped[str] = mapped_column(primary_key=True)
    last_id: Mapped[int]


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('parent','child-view','admin')"),
        CheckConstraint("(role = 'child-view') = (child_id IS NOT NULL)"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(180))
    role: Mapped[str] = mapped_column(String(16))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), unique=True)
    failures: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[int] = mapped_column(default=0)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True, default=next_public_id)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name_cipher: Mapped[str] = mapped_column(Text)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[int] = mapped_column(index=True)


class LoginWindow(Base):
    """An IP keyed HMAC supports throttling without retaining the raw IP."""
    __tablename__ = "login_windows"
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int]
    expires_at: Mapped[int] = mapped_column(index=True)


class AuditEntry(Base):
    __tablename__ = "audits"
    __table_args__ = (Index("ix_audits_child_ts", "child_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ts: Mapped[int]
    action: Mapped[str] = mapped_column(String(64))
    ip_cipher: Mapped[str] = mapped_column(Text)
    before_cipher: Mapped[str] = mapped_column(Text)
    after_cipher: Mapped[str] = mapped_column(Text)
