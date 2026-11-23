"""Parent authorization and the administrator's separate report of local SCM removal."""
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import ForeignKey, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from server.audit import record
from server.auth import SESSION_COOKIE, SESSION_LIFETIME, require_csrf, require_parent
from server.db import Base, get_db
from server.family_models import Erasure
from server.models import Child, LoginSession, User
from server.retention import ERASURE_ACK_HISTORY
from server.security import digest
from server.sync_models import Device
from shared.protocol import WireModel


class Removal(Base):
    __tablename__ = "lab_removals"
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    authorized_at: Mapped[int]
    reported_at: Mapped[int | None]


class RemovalAction(WireModel):
    stage: Literal["authorize", "report_removed"]


router = APIRouter(prefix="/api")


@router.post("/children/{child_id}/devices/{device_id}/lab-removal", dependencies=[Depends(require_csrf)])
def lab_removal(child_id: int, device_id: str, data: RemovalAction, request: Request,
                db: Session = Depends(get_db), parent: User = Depends(require_parent)):
    db.execute(text("BEGIN IMMEDIATE"))
    now = int(time.time())
    login = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(request.cookies[SESSION_COOKIE]),
                                                LoginSession.user_id == parent.id, LoginSession.expires_at > now))
    if login is None or now - (login.expires_at - SESSION_LIFETIME) > 300:
        raise HTTPException(401, "Hãy đăng nhập lại bằng mật khẩu phụ huynh để xác nhận thao tác gỡ.")
    device = db.scalar(select(Device).join(Child, Child.id == Device.child_id).where(
        Device.id == device_id, Device.child_id == child_id, Child.parent_id == parent.id))
    if device is None:
        receipt = db.scalar(select(Erasure).where(Erasure.device_id == device_id, Erasure.parent_id == parent.id,
                            (Erasure.acknowledged_at.is_(None) | (Erasure.acknowledged_at >= now - ERASURE_ACK_HISTORY))))
        if receipt is not None:
            # This receipt proves whose device was erased. SCM cannot acknowledge a tray wipe,
            # and erasure must not recreate a child audit or extend the receipt's lifetime.
            return {"authorized": True, "reported_removed": False, "profile_erased": True}
        # Issued only after CSRF and fresh parent login. Generic proxy/route 404s never authorize cleanup.
        raise HTTPException(404, detail={"code": "lab_removal_target_unavailable"})
    removal = db.get(Removal, device_id)
    if data.stage == "authorize":
        if removal is None:
            removal = Removal(device_id=device_id, authorized_at=now)
            db.add(removal)
            record(db, request, parent, child_id, "lab.removal_authorized", {"revoked": device.revoked},
                   {"device_id": device_id, "revoked": True})
        device.revoked = True
        device.access_until = device.refresh_until = 0
    elif removal is None:
        raise HTTPException(409, "Chưa có ủy quyền gỡ cho thiết bị.")
    elif removal.reported_at is None:
        removal.reported_at = now
        record(db, request, parent, child_id, "lab.removal_reported", {}, {"device_id": device_id})
    db.commit()
    return {"authorized": True, "reported_removed": removal.reported_at is not None}
