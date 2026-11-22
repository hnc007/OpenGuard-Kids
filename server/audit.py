import json
import time

from fastapi import Request
from sqlalchemy.orm import Session

from .db import get_vault
from .models import AuditEntry, User


def record(db: Session, request: Request, actor: User, child_id: int | None,
           action: str, before: dict, after: dict) -> None:
    """Persist in the same transaction as the change. Callers pass only non-secret values."""
    vault = get_vault(request)
    address = request.client.host if request.client else "unknown"
    db.add(AuditEntry(child_id=child_id, actor_id=actor.id, ts=int(time.time()), action=action,
                      ip_cipher=vault.encrypt(address),
                      before_cipher=vault.encrypt(json.dumps(before, ensure_ascii=False)),
                      after_cipher=vault.encrypt(json.dumps(after, ensure_ascii=False))))
