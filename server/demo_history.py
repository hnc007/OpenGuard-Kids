"""Synthetic events for a paired simulator in the original demo family."""
from datetime import datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from shared.protocol import UsagePayload
from .models import Child, User
from .security import Vault
from .sync_models import Device, UsageEvent


def seed_history(db: Session, vault: Vault, device_id: UUID) -> int:
    row = db.execute(select(Device, Child).join(Child, Child.id == Device.child_id)
        .join(User, User.id == Child.parent_id).where(Device.id == str(device_id), Device.revoked.is_(False),
            User.role == "parent", User.username == "demo.parent")).one_or_none()
    if row is None:
        raise ValueError("Cần một thiết bị đang ghép với hồ sơ mẫu của demo.parent.")
    device, child = row
    label = vault.decrypt(device.label).casefold()
    if vault.decrypt(child.name_cipher) != "An (hồ sơ mẫu)" or not ("demo" in label or "mô phỏng" in label):
        raise ValueError("Chỉ thêm lịch sử vào hồ sơ mẫu An và thiết bị tên chứa “demo” hoặc “mô phỏng”.")
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    now = datetime.now(zone)
    count = 0
    for offset in range(10):
        date = now.date() - timedelta(days=offset)
        ts = min(int(now.timestamp()), int(datetime.combine(date, time(16), zone).timestamp()))
        events = [("app_stop", "demo-reader.exe", 60 * (12 + date.day % 15)),
                  ("app_stop", "demo-paint.exe", 60 * (7 + date.day % 6)),
                  ("domain_query", "example.org", 0)]
        if date.day % 2 == 0:
            events.append(("blocked_domain", "example.net", 0))
        for event_type, subject, duration in events:
            data = UsagePayload(ts=ts, device_id=device_id, child_id=child.id, type=event_type, subject=subject,
                                duration_sec=duration, policy_id=1).model_dump(mode="json")
            key = uuid5(device_id, f"openguard-demo:{date}:{event_type}:{subject}")
            data.update(event_key=str(key), subject=vault.encrypt(subject))
            count += db.execute(insert(UsageEvent).values(**data)
                .on_conflict_do_nothing(index_elements=["device_id", "event_key"])).rowcount
    return count
