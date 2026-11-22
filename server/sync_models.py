"""Persistence for the family/device boundary. Times are UTC epoch seconds."""

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.db import Base


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    label: Mapped[str]
    fingerprint_hash: Mapped[str] = mapped_column(unique=True)
    access_hash: Mapped[str]
    access_until: Mapped[int]
    refresh_hash: Mapped[str]
    erasure_hash: Mapped[str | None]
    local_wiped_at: Mapped[int | None]
    refresh_until: Mapped[int]
    revoked: Mapped[bool]
    last_seen: Mapped[int | None]
    last_status: Mapped[str | None]
    remaining_seconds: Mapped[int | None]
    used_seconds: Mapped[int]
    policy_version: Mapped[int]
    consent_at: Mapped[int]
    consent_parent_id: Mapped[int]
    consent_scope: Mapped[str]


class PairCode(Base):
    __tablename__ = "pair_codes"
    digest: Mapped[str] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int]
    created_at: Mapped[int]
    expires_at: Mapped[int]


class PairAttempt(Base):
    __tablename__ = "pair_attempts"
    source_hash: Mapped[str] = mapped_column(primary_key=True)
    started: Mapped[int]
    count: Mapped[int]


class Policy(Base):
    __tablename__ = "policies"
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int]
    body: Mapped[str] = mapped_column(Text)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (UniqueConstraint("device_id", "event_key", name="uq_device_event"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str]
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    ts: Mapped[int] = mapped_column(index=True)
    type: Mapped[str]
    subject: Mapped[str]
    duration_sec: Mapped[int]
    policy_id: Mapped[int]
