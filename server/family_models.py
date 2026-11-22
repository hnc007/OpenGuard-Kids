from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, next_public_id


class FamilyRequest(Base):
    __tablename__ = "family_requests"
    id: Mapped[int] = mapped_column(primary_key=True, default=next_public_id)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    domain_cipher: Mapped[str]
    status: Mapped[str]
    answer_cipher: Mapped[str]
    created_at: Mapped[int]
    resolved_at: Mapped[int | None]


class Command(Base):
    __tablename__ = "commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    action: Mapped[str]
    created_at: Mapped[int]
    expires_at: Mapped[int]
    acknowledged_at: Mapped[int | None]


class Erasure(Base):
    __tablename__ = "erasures"
    device_id: Mapped[str] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    proof_hash: Mapped[str]
    created_at: Mapped[int]
    acknowledged_at: Mapped[int | None]
