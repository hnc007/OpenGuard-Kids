"""Device enrollment and synchronization. Browser writes require the parent session and CSRF."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.audit import record
from server.auth import current_user, owned_child, require_csrf, require_parent
from server.db import get_db, get_vault
from server.models import User
from server.sync_models import Device, PairAttempt, PairCode, Policy, UsageEvent
from shared.protocol import EventBatch, PolicyChange, PolicySettings, WireModel, canonical

router = APIRouter(prefix="/api")
DAY = 86400
WRITE = [Depends(require_csrf)]
CONSENT_SCOPE = "visible_usage_quota_app_dns_reporting_v1"


class Consent(WireModel):
    consent: bool = False
    child_informed: bool = False
    child_agrees: bool = False


class Enrollment(WireModel):
    code: str = Field(pattern=r"^[A-Z2-9]{8}$")
    installation_id: UUID
    label: str = Field(min_length=1, max_length=64, pattern=r"^[\w .()-]+$")


class Refresh(WireModel):
    refresh_token: SecretStr = Field(min_length=32, max_length=128)


class Heartbeat(WireModel):
    status: Literal["visible", "paused"]
    simulate_commands: bool = False
    used_seconds: int = Field(ge=0, le=86400)
    remaining_seconds: int | None = Field(default=None, ge=0, le=2147483647)
    policy_version: int = Field(ge=0)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def keyed(vault, purpose: str, value: str) -> str:
    return hmac.new(vault.derive(purpose), value.encode(), "sha256").hexdigest()


def signing_key(vault) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(vault.derive("ed25519-policy-v1")[:32])


def issue_tokens(now: int) -> tuple[dict, dict]:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    public = {"access_token": access, "refresh_token": refresh, "access_until": now + 900}
    hashed = {"access_hash": digest(access), "refresh_hash": digest(refresh),
              "access_until": now + 900, "refresh_until": now + 30 * DAY}
    return public, hashed


def authorized_device(db: Session, device_id: str, token: str) -> Device:
    device = db.scalar(select(Device).where(Device.id == device_id, Device.access_hash == digest(token),
                                            Device.access_until > int(time.time()), Device.revoked.is_(False)))
    if not device:
        raise HTTPException(401, "Phiên thiết bị không hợp lệ.")
    return device


def device_auth(device_id: str, request: Request, db: Session = Depends(get_db)) -> Device:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not 32 <= len(token) <= 128:
        raise HTTPException(401, "Cần xác thực thiết bị.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        # The same session commits the heartbeat, events or command ACK. Order
        # authorization and that write together with device revocation.
        db.execute(text("BEGIN IMMEDIATE"))
    return authorized_device(db, device_id, token)


def policy_for(db: Session, child_id: int, vault) -> dict:
    row = db.get(Policy, child_id)
    if row is None:
        return {"version": 1, **PolicySettings().model_dump()}
    return {"version": row.version, **json.loads(vault.decrypt(row.body))}


@router.get("/children/{child_id}/policy")
def read_policy(child_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_child(db, user, child_id)
    return policy_for(db, child_id, get_vault(request))


@router.put("/children/{child_id}/policy", dependencies=WRITE)
def update_policy(child_id: int, changes: PolicyChange, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_parent)):
    owned_child(db, user, child_id)
    vault = get_vault(request)
    previous = policy_for(db, child_id, vault)
    if changes.version != previous["version"]:
        raise HTTPException(409, "Quy tắc đã đổi. Vui lòng tải lại.")
    values = {**previous, **changes.model_dump(exclude_unset=True)}
    values.pop("version")
    body = vault.encrypt(json.dumps(values, ensure_ascii=False))
    # Materialize the default row, then compare-and-swap inside the same write transaction.
    db.execute(insert(Policy).values(child_id=child_id, body=body, version=1).on_conflict_do_nothing())
    written = db.execute(update(Policy).where(Policy.child_id == child_id, Policy.version == changes.version)
                         .values(version=changes.version + 1, body=body))
    if written.rowcount != 1:
        raise HTTPException(409, "Quy tắc đã đổi. Vui lòng tải lại.")
    result = {"version": changes.version + 1, **values}
    record(db, request, user, child_id, "policy.update", previous, result)
    db.commit()
    return result


@router.post("/children/{child_id}/pairing", dependencies=WRITE, status_code=201)
def create_pairing(child_id: int, consent: Consent, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_parent)):
    owned_child(db, user, child_id)
    if not (consent.consent and consent.child_informed and consent.child_agrees):
        raise HTTPException(422, "Cần sự đồng ý của phụ huynh và của trẻ sau khi đã giải thích.")
    now = int(time.time())
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    db.execute(delete(PairCode).where((PairCode.child_id == child_id) | (PairCode.expires_at <= now)))
    db.add(PairCode(digest=keyed(get_vault(request), "pair-code", code), child_id=child_id, parent_id=user.id,
                    created_at=now, expires_at=now + 600))
    record(db, request, user, child_id, "pairing.consent", {}, {**consent.model_dump(), "scope": CONSENT_SCOPE})
    db.commit()
    return {"code": code, "expires_at": now + 600}


def limit_pairing(db: Session, request: Request, now: int, vault):
    source = request.client.host if request.client else "local"
    bucket = keyed(vault, "pair-limit", source)
    db.execute(delete(PairAttempt).where(PairAttempt.started <= now - 600))
    stmt = insert(PairAttempt).values(source_hash=bucket, started=now, count=1)
    count = db.scalar(stmt.on_conflict_do_update(index_elements=["source_hash"],
                      set_={"count": PairAttempt.count + 1}).returning(PairAttempt.count))
    db.commit()  # Failed enrollments must consume their attempt too.
    if count > 10:
        raise HTTPException(429, "Đã thử ghép đôi nhiều lần. Hãy chờ 10 phút.")


@router.post("/enroll", status_code=201)
def enroll(data: Enrollment, request: Request, db: Session = Depends(get_db)):
    vault, now = get_vault(request), int(time.time())
    limit_pairing(db, request, now, vault)
    code = db.execute(delete(PairCode).where(PairCode.digest == keyed(vault, "pair-code", data.code),
                                            PairCode.expires_at > now)
                      .returning(PairCode.child_id, PairCode.parent_id, PairCode.created_at)).first()
    if code is None:
        raise HTTPException(400, "Mã không hợp lệ hoặc đã hết hạn.")
    public, hashed = issue_tokens(now)
    device_id = str(uuid4())
    erasure_token = secrets.token_urlsafe(48)
    db.add(Device(id=device_id, child_id=code.child_id, label=vault.encrypt(data.label),
                  fingerprint_hash=keyed(vault, "installation", str(data.installation_id)),
                  erasure_hash=digest(erasure_token), local_wiped_at=None,
                  **hashed, revoked=False, last_seen=None, used_seconds=0, policy_version=0,
                  consent_at=code.created_at, consent_parent_id=code.parent_id, consent_scope=CONSENT_SCOPE))
    try:
        record(db, request, db.get(User, code.parent_id), code.child_id, "device.enroll", {},
               {"device_id": device_id, "label": data.label})
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "Lần cài đặt đã ghép đôi.") from error
    key = signing_key(vault).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"device_id": device_id, "child_id": code.child_id, **public, "erasure_token": erasure_token,
            "verification_key": base64.b64encode(key).decode()}


@router.post("/devices/{device_id}/refresh")
def refresh(device_id: str, credential: Refresh, db: Session = Depends(get_db)):
    db.execute(text("BEGIN IMMEDIATE"))
    now = int(time.time())
    public, hashed = issue_tokens(now)
    changed = db.execute(update(Device).where(Device.id == device_id, Device.revoked.is_(False),
                         Device.refresh_hash == digest(credential.refresh_token.get_secret_value()),
                         Device.refresh_until > now).values(**hashed))
    if changed.rowcount != 1:
        raise HTTPException(401, "Cần ghép đôi lại thiết bị.")
    db.commit()
    return public


@router.get("/children/{child_id}/devices")
def list_devices(child_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_child(db, user, child_id)
    devices = db.scalars(select(Device).where(Device.child_id == child_id).order_by(Device.id))
    vault = get_vault(request)
    return [{"id": d.id, "label": vault.decrypt(d.label), "last_seen": d.last_seen, "revoked": d.revoked,
             "status": d.last_status, "remaining_seconds": d.remaining_seconds,
             "used_seconds": d.used_seconds, "policy_version": d.policy_version} for d in devices]


@router.delete("/children/{child_id}/devices/{device_id}", status_code=204, dependencies=WRITE)
def revoke(child_id: int, device_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_parent)):
    owned_child(db, user, child_id)
    changed = db.execute(update(Device).where(Device.id == device_id, Device.child_id == child_id)
                         .values(revoked=True, access_until=0, refresh_until=0))
    if not changed.rowcount:
        raise HTTPException(404, "Không tìm thấy thiết bị.")
    record(db, request, user, child_id, "device.revoke", {"device_id": device_id}, {"revoked": True})
    db.commit()
    return Response(status_code=204)


@router.get("/devices/{device_id}/policy")
def signed_policy(request: Request, device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    vault, now = get_vault(request), int(time.time())
    body = {**policy_for(db, device.child_id, vault), "device_id": device.id, "child_id": device.child_id,
            "issued_at": now, "expires_at": now + 7 * DAY, "issuer": "Phụ huynh"}
    return {"policy": body, "signature": base64.b64encode(signing_key(vault).sign(canonical(body))).decode()}


@router.post("/devices/{device_id}/heartbeat")
def heartbeat(data: Heartbeat, device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    from server.family import pending_commands

    now = int(time.time())
    device.last_seen = now
    device.used_seconds = data.used_seconds
    device.last_status = data.status
    device.remaining_seconds = data.remaining_seconds if data.status == "visible" else None
    device.policy_version = data.policy_version
    version = db.scalar(select(Policy.version).where(Policy.child_id == device.child_id)) or 1
    commands = pending_commands(db, device.id) if data.status == "visible" or data.simulate_commands else []
    db.commit()
    return {"server_time": now, "policy_version": version, "commands": commands}


@router.post("/devices/{device_id}/events")
def events(data: EventBatch, request: Request, device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    now, vault = int(time.time()), get_vault(request)
    inserted = 0
    for item in data.events:
        event = item.payload
        if device.id != str(event.device_id) or device.child_id != event.child_id:
            raise HTTPException(403, "Sự kiện sai phạm vi thiết bị.")
        if event.ts > now + 120 or event.ts < now - 90 * DAY:
            raise HTTPException(422, "Thời điểm sự kiện ngoài khoảng lưu trữ.")
        row = {**event.model_dump(mode="json"), "subject": vault.encrypt(event.subject), "event_key": str(item.event_id)}
        inserted += db.execute(insert(UsageEvent).values(**row)
                               .on_conflict_do_nothing(index_elements=["device_id", "event_key"])).rowcount
    db.commit()
    return {"received": len(data.events), "inserted": inserted}
