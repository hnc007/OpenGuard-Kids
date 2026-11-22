"""Parent and child views share the same report and policy evidence."""
import asyncio
import hmac
import time
from collections import Counter
from datetime import datetime, time as day_time, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import AliasChoices, Field, model_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from shared.protocol import WireModel, reporting_domain
from . import audit
from .auth import current_user, owned_child, require_csrf, require_parent
from .db import get_db
from .family_models import Command, Erasure, FamilyRequest
from .models import User
from .retention import COMMAND_HISTORY, ERASURE_ACK_HISTORY, FAMILY_HISTORY
from .security import digest
from .sync import authorized_device, device_auth
from .sync_models import Device, PairAttempt, PairCode, UsageEvent

router = APIRouter(prefix="/api")
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
WRITE = [Depends(require_csrf)]
DAY = 86400


class NewRequest(WireModel):
    kind: Literal["time", "domain"]
    domain: str = Field(default="", max_length=253)

    @model_validator(mode="after")
    def minimise(self):
        if self.kind == "domain":
            self.domain = reporting_domain(self.domain)
        elif self.domain:
            raise ValueError("Yêu cầu thêm giờ chỉ ghi loại yêu cầu.")
        return self


class Decision(WireModel):
    status: Literal["approved", "declined"]
    answer: str = Field(min_length=1, max_length=240, pattern=r"\S")
    device_id: str | None = Field(default=None, max_length=36)


class NewCommand(WireModel):
    action: Literal["lock", "unlock", "add_15"]


class ConfirmDelete(WireModel):
    confirm: Literal[True]
    child_id: int


class WipeProof(WireModel):
    erasure_token: str = Field(min_length=16, max_length=256,
                               validation_alias=AliasChoices("erasure_token", "refresh_token"))
    acknowledge: bool = False


@router.get("/children/{child_id}/reports")
def report(child_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_child(db, user, child_id)
    now = datetime.now(ZONE)
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=3)
    start = datetime.combine(first, datetime.min.time(), ZONE)
    rows = db.scalars(select(UsageEvent).where(UsageEvent.child_id == child_id,
        UsageEvent.ts >= int(start.timestamp()), UsageEvent.ts <= int(now.timestamp())).execution_options(yield_per=256))
    days, weeks, apps, domains = Counter(), Counter(), Counter(), Counter()
    blocked = 0
    for row in rows:
        date = datetime.fromtimestamp(row.ts, ZONE).date()
        if row.type == "app_stop":
            # An app_stop ends a measured interval. Attribute each part to its
            # local calendar day, including sessions that cross Sunday midnight.
            end = row.ts
            cursor = max(int(start.timestamp()), end - row.duration_sec)
            while cursor < end:
                local_day = datetime.fromtimestamp(cursor, ZONE).date()
                midnight = datetime.combine(local_day + timedelta(days=1), day_time.min, ZONE)
                boundary = min(end, int(midnight.timestamp()))
                seconds = boundary - cursor
                days[local_day] += seconds
                weeks[local_day - timedelta(days=local_day.weekday())] += seconds
                if local_day >= monday:
                    apps[request.app.state.vault.decrypt(row.subject)] += seconds
                cursor = boundary
            continue
        if date < monday:
            continue
        if row.type in {"domain_query", "blocked_domain"}:
            domains[request.app.state.vault.decrypt(row.subject)] += 1
        if row.type in {"blocked_app", "blocked_domain"}:
            blocked += 1
    return {
        "today_seconds": days[today], "week_seconds": weeks[monday], "blocked": blocked,
        "days": [{"date": str(d), "seconds": days[d]} for d in (today - timedelta(days=i) for i in range(6,-1,-1))],
        "weeks": [{"date": str(d), "seconds": weeks[d]} for d in (monday - timedelta(weeks=i) for i in range(3,-1,-1))],
        "apps": apps.most_common(10), "domains": domains.most_common(10),
    }


@router.get("/children/{child_id}/requests")
def requests(child_id: int, request: Request, offset: int = Query(0, ge=0),
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_child(db, user, child_id)
    vault = request.app.state.vault
    cutoff = int(time.time()) - FAMILY_HISTORY
    rows = db.scalars(select(FamilyRequest).where(FamilyRequest.child_id == child_id, FamilyRequest.created_at >= cutoff)
                      .order_by(FamilyRequest.id.desc()).offset(offset).limit(50))
    return [{"id": r.id, "kind": r.kind, "domain": vault.decrypt(r.domain_cipher), "status": r.status,
             "answer": vault.decrypt(r.answer_cipher), "created_at": r.created_at} for r in rows]


@router.post("/children/{child_id}/requests", dependencies=WRITE, status_code=201)
def ask(child_id: int, body: NewRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role != "child-view":
        raise HTTPException(403, "Chỉ tài khoản của em được gửi yêu cầu này.")
    db.execute(text("BEGIN IMMEDIATE"))
    owned_child(db, user, child_id)
    now = int(time.time())
    count = db.scalar(select(func.count()).select_from(FamilyRequest).where(
        FamilyRequest.child_id == child_id, FamilyRequest.created_at >= now - 3600))
    if count >= 12:
        raise HTTPException(429, "Em đã gửi nhiều yêu cầu. Hãy thử lại sau.")
    vault = request.app.state.vault
    item = FamilyRequest(child_id=child_id, kind=body.kind, domain_cipher=vault.encrypt(body.domain),
                         status="pending", answer_cipher=vault.encrypt(""), created_at=now)
    db.add(item)
    db.commit()
    return {"id": item.id, "status": "pending"}


def enqueue(db, child_id, device_id, action):
    device = db.scalar(select(Device).where(Device.id == device_id, Device.child_id == child_id, Device.revoked.is_(False)))
    if device is None:
        raise HTTPException(404, "Cần chọn thiết bị còn ghép với hồ sơ.")
    now = int(time.time())
    cmd = Command(id=str(uuid4()), device_id=device.id, action=action, created_at=now, expires_at=now + 120)
    db.add(cmd)
    return cmd


@router.post("/children/{child_id}/requests/{item_id}", dependencies=WRITE)
def decide(child_id: int, item_id: int, body: Decision, request: Request, db: Session = Depends(get_db),
           parent: User = Depends(require_parent)):
    db.execute(text("BEGIN IMMEDIATE"))
    owned_child(db, parent, child_id)
    now = int(time.time())
    item = db.scalar(select(FamilyRequest).where(FamilyRequest.id == item_id, FamilyRequest.child_id == child_id,
                                                  FamilyRequest.created_at >= now - FAMILY_HISTORY))
    if item is None:
        raise HTTPException(404)
    updated = db.execute(update(FamilyRequest).where(FamilyRequest.child_id == child_id, FamilyRequest.id == item_id, FamilyRequest.status == "pending")
        .values(status=body.status, resolved_at=now, answer_cipher=request.app.state.vault.encrypt(body.answer.strip())))
    if not updated.rowcount:
        raise HTTPException(409, "Yêu cầu đã có phản hồi.")
    if item.kind == "time" and body.status == "approved":
        enqueue(db, child_id, body.device_id, "add_15")
    audit.record(db, request, parent, child_id, "request.decision", {"status":"pending"}, {"id":item_id,"status":body.status})
    db.commit()
    return {"status": body.status}


@router.post("/children/{child_id}/devices/{device_id}/commands", dependencies=WRITE, status_code=201)
def queue_command(child_id: int, device_id: str, body: NewCommand, request: Request,
                  db: Session = Depends(get_db), parent: User = Depends(require_parent)):
    db.execute(text("BEGIN IMMEDIATE"))
    owned_child(db, parent, child_id)
    cmd = enqueue(db, child_id, device_id, body.action)
    audit.record(db, request, parent, child_id, "command.queued", {}, {"id":cmd.id,"action":body.action})
    db.commit()
    return {"id": cmd.id, "status":"queued", "expires_at": cmd.expires_at}


def pending_commands(db: Session, device_id: str):
    rows = db.scalars(select(Command).where(Command.device_id == device_id, Command.acknowledged_at.is_(None),
        Command.expires_at > int(time.time())).order_by(Command.created_at, text("commands.rowid")).limit(50))
    return [{"id": r.id, "action": r.action, "expires_at": r.expires_at} for r in rows]


@router.get("/children/{child_id}/commands")
def command_history(child_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_child(db, user, child_id)
    now = int(time.time())
    rows = db.execute(select(Command, Device.label).join(Device, Command.device_id == Device.id)
                      .where(Device.child_id == child_id, Command.expires_at >= now - COMMAND_HISTORY)
                      .order_by(Command.created_at.desc(), text("commands.rowid DESC")).limit(50))
    return [{"action":cmd.action, "device":request.app.state.vault.decrypt(label), "created_at":cmd.created_at,
             "status":"acknowledged" if cmd.acknowledged_at else "expired" if cmd.expires_at <= now else "queued"}
            for cmd, label in rows]


@router.post("/devices/{device_id}/commands/{cmd_id}/ack")
def acknowledge_command(cmd_id: str, device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    now = int(time.time())
    cmd = db.scalar(select(Command).where(Command.id == cmd_id, Command.device_id == device.id,
                                          Command.expires_at >= now - COMMAND_HISTORY))
    if cmd is None:
        raise HTTPException(404)
    if cmd.acknowledged_at is None:
        if now >= cmd.expires_at:
            raise HTTPException(410, "Lệnh đã hết hạn.")
        cmd.acknowledged_at = now
        db.commit()
    return {"status":"acknowledged"}


@router.websocket("/devices/{device_id}/commands/ws")
async def command_socket(ws: WebSocket, device_id: str):
    scheme, _, token = ws.headers.get("authorization","").partition(" ")
    if scheme.lower() != "bearer" or ws.headers.get("origin"):
        await ws.close(code=1008)
        return
    def fetch():
        with ws.app.state.sessions() as db:
            authorized_device(db, device_id, token)
            return pending_commands(db, device_id)
    try:
        batch = await asyncio.to_thread(fetch)
        await ws.accept()
        sent = {}
        while True:
            now = time.monotonic()
            fresh = [cmd for cmd in batch if now - sent.get(cmd["id"], float("-inf")) >= 5]
            if fresh:
                await ws.send_json({"commands":fresh})
            sent = {cmd["id"]: (now if cmd in fresh else sent[cmd["id"]]) for cmd in batch}
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=1)
            except TimeoutError:
                pass
            else:
                if msg["type"] != "websocket.disconnect":
                    await ws.close(code=1008)
                return
            batch = await asyncio.to_thread(fetch)
    except HTTPException:
        await ws.close(code=1008)
    except WebSocketDisconnect:
        return


@router.delete("/children/{child_id}", dependencies=WRITE)
def erase(child_id: int, body: ConfirmDelete, db: Session = Depends(get_db), parent: User = Depends(require_parent)):
    if body.child_id != child_id:
        raise HTTPException(400, "Xác nhận chưa khớp hồ sơ.")
    # Serialize with refresh rotation and event ingestion before copying deletion credentials.
    db.execute(text("BEGIN IMMEDIATE"))
    child = owned_child(db, parent, child_id)
    devices = list(db.scalars(select(Device).where(Device.child_id == child_id)))
    for device in devices:
        db.add(Erasure(device_id=device.id, parent_id=parent.id, proof_hash=device.erasure_hash,
                       created_at=int(time.time()), acknowledged_at=device.local_wiped_at))
    db.delete(child)
    db.commit()
    return {"server_erased": True, "devices_pending": sum(d.local_wiped_at is None for d in devices)}


@router.get("/erasures")
def receipts(db: Session = Depends(get_db), parent: User = Depends(require_parent)):
    cutoff = int(time.time()) - ERASURE_ACK_HISTORY
    rows = db.scalars(select(Erasure).where(Erasure.parent_id == parent.id,
        (Erasure.acknowledged_at.is_(None) | (Erasure.acknowledged_at >= cutoff))).order_by(Erasure.created_at.desc()))
    return [{"device_id":r.device_id, "created_at":r.created_at, "acknowledged":r.acknowledged_at is not None} for r in rows]


@router.post("/devices/{device_id}/erasure")
def erase_receipt(device_id: str, body: WipeProof, db: Session = Depends(get_db)):
    if body.acknowledge:
        db.execute(text("BEGIN IMMEDIATE"))
    receipt = db.get(Erasure, device_id)
    proof = digest(body.erasure_token)
    if receipt is None:
        # A revoked device or one whose refreshed credentials were lost can already
        # have wiped its local partition before the parent requests server deletion.
        if body.acknowledge:
            device = db.get(Device, device_id)
            if device is not None and hmac.compare_digest(device.erasure_hash or "", proof):
                device.local_wiped_at = device.local_wiped_at or int(time.time())
                device.revoked = True
                device.access_until = device.refresh_until = 0
                db.commit()
                return {"erase": True}
        raise HTTPException(404)
    if (receipt.acknowledged_at is not None and receipt.acknowledged_at < int(time.time()) - ERASURE_ACK_HISTORY
            or not hmac.compare_digest(receipt.proof_hash, proof)):
        raise HTTPException(404)
    if body.acknowledge and receipt.acknowledged_at is None:
        receipt.acknowledged_at = int(time.time())
        db.commit()
    return {"erase":True}


def prune_expired(db: Session):
    now = int(time.time())
    cutoff = now - FAMILY_HISTORY
    db.execute(delete(UsageEvent).where(UsageEvent.ts < cutoff))
    db.execute(delete(FamilyRequest).where(FamilyRequest.created_at < cutoff))
    db.execute(delete(Command).where(Command.expires_at < now - COMMAND_HISTORY))
    db.execute(delete(PairCode).where(PairCode.expires_at <= now))
    db.execute(delete(PairAttempt).where(PairAttempt.started < now - DAY))
    db.execute(delete(Erasure).where(Erasure.acknowledged_at < now - ERASURE_ACK_HISTORY))
