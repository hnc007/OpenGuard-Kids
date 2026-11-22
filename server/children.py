import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit
from .auth import child_scope, current_user, owned_child, require_csrf, require_parent
from .accounts import create_user
from .db import get_db, get_vault
from .models import AuditEntry, Child, User
from .retention import FAMILY_HISTORY

router = APIRouter(prefix="/api", tags=["Gia đình"])


class NewChild(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def printable_alias(cls, value: str):
        if not value.isprintable():
            raise ValueError("Hãy dùng biệt danh có ký tự hiển thị được.")
        return value


class ChildAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=256)


@router.post("/children/{child_id}/account", status_code=201, dependencies=[Depends(require_csrf)])
def add_child_account(child_id: int, body: ChildAccount, request: Request,
                      user: User = Depends(require_parent), db: Session = Depends(get_db)):
    owned_child(db, user, child_id)
    try:
        account = create_user(db, body.username, body.password, "child-view", child_id)
        result = {"username": account.username, "role": account.role, "child_id": child_id}
        audit.record(db, request, user, child_id, "child.account.created", {}, {"role": account.role})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Hồ sơ đã có tài khoản hoặc tên đăng nhập đã được sử dụng.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@router.get("/children")
def list_children(request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    children = db.scalars(select(Child).where(child_scope(user)).order_by(Child.id).offset(offset).limit(limit))
    return [{"id": child.id, "name": get_vault(request).decrypt(child.name_cipher)} for child in children]


@router.post("/children", status_code=201, dependencies=[Depends(require_csrf)])
def add_child(body: NewChild, request: Request, user: User = Depends(require_parent), db: Session = Depends(get_db)):
    child = Child(parent_id=user.id, name_cipher=get_vault(request).encrypt(body.name))
    db.add(child)
    db.flush()
    audit.record(db, request, user, child.id, "child.created", {}, {"name": body.name})
    db.commit()
    return {"id": child.id, "name": body.name}


@router.get("/children/{child_id}/audit")
def list_audit(child_id: int, request: Request, offset: int = Query(0, ge=0),
               limit: int = Query(50, ge=1, le=100), user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    owned_child(db, user, child_id)
    entries = db.execute(select(AuditEntry, User.username).outerjoin(User, User.id == AuditEntry.actor_id)
        .where(AuditEntry.child_id == child_id, AuditEntry.ts >= int(time.time()) - FAMILY_HISTORY)
        .order_by(AuditEntry.ts.desc(), AuditEntry.id.desc())
        .offset(offset).limit(limit))
    vault = get_vault(request)
    result = []
    for entry, actor in entries:
        item = {"id": entry.id, "action": entry.action, "actor": actor,
                "ts": datetime.fromtimestamp(entry.ts, UTC).isoformat(),
                "before": json.loads(vault.decrypt(entry.before_cipher)),
                "after": json.loads(vault.decrypt(entry.after_cipher))}
        if user.role == "parent":
            item["ip"] = vault.decrypt(entry.ip_cipher)
        result.append(item)
    return result


@router.get("/admin/status")
def admin_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(403, "Thao tác này dành cho quản trị viên.")
    return {"accounts": db.scalar(select(func.count(User.id))), "children": db.scalar(select(func.count(Child.id)))}
