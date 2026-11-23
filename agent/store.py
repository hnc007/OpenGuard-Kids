"""Device-local SQLite cache, bounded outbox and protected enrollment credentials."""

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from weakref import WeakMethod

from shared.protocol import UsageEnvelope, UsagePayload, canonical, verified_policy


ENROLLMENT_FIELDS = ("device_id", "child_id", "origin", "verification_key")


def same_enrollment(current, expected):
    return bool(current and not current.get("erase_pending") and
                all(current.get(field) == expected.get(field) for field in ENROLLMENT_FIELDS))


class WindowsProtection:
    """DPAPI encrypts for the account running the simulator (service identity in a lab)."""

    def encrypt(self, raw: bytes) -> bytes:
        import win32crypt
        return win32crypt.CryptProtectData(raw, "OpenGuard Kids", None, None, None, 1)

    def decrypt(self, raw: bytes) -> bytes:
        import pywintypes
        import win32crypt
        try:
            return win32crypt.CryptUnprotectData(raw, None, None, None, 1)[1]
        except pywintypes.error as error:
            raise ValueError("Không mở được bản ghi bằng DPAPI của tài khoản hiện tại.") from error


class AgentStore:
    def __init__(self, path: str | Path, protection=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protection = protection if protection is not None else WindowsProtection()
        self.activity_lock = threading.RLock()
        self._erasure_listeners = []
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS enrollment (id INTEGER PRIMARY KEY CHECK(id=1), data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS cached_policy (device_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                                                         data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, ts INTEGER NOT NULL, data BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_outbox_ts ON outbox(ts);
                CREATE TABLE IF NOT EXISTS outbox_quarantine (
                    event_id TEXT PRIMARY KEY REFERENCES outbox(event_id) ON DELETE CASCADE
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def identity(self) -> dict | None:
        with self.connection() as db:
            return self._read_identity(db)

    def _read_identity(self, db) -> dict | None:
        row = db.execute("SELECT data FROM enrollment WHERE id=1").fetchone()
        return json.loads(self.protection.decrypt(row[0])) if row else None

    def on_local_wipe(self, callback):
        """Release live session state under the sampler lock immediately after SQLite commits."""
        with self.activity_lock:
            self._erasure_listeners = [ref for ref in self._erasure_listeners if ref() is not None]
            self._erasure_listeners.append(WeakMethod(callback))

    def counter(self, identity):
        from agent.screen_time import CounterStore

        binding = {field: identity[field] for field in ENROLLMENT_FIELDS}

        @contextmanager
        def enrolled_transaction():
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if not same_enrollment(self._read_identity(db), binding):
                    raise ValueError("Lần ghép máy đã kết thúc. Đã dừng ghi bộ đếm.")
                yield db

        return CounterStore(self.path, binding["device_id"], write_transaction=enrolled_transaction)

    def save_identity(self, identity: dict, *, previous: dict | None = None, new: bool = False):
        sealed = self.protection.encrypt(canonical(identity))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read_identity(db)
            if (new and current is not None) or (previous is not None and previous != current):
                raise ValueError("Danh tính thiết bị đã đổi. Hãy đồng bộ lại.")
            db.execute("INSERT OR REPLACE INTO enrollment VALUES(1, ?)", (sealed,))

    def save_policy(self, envelope: dict, device_id: str, verification_key: str, now: int | None = None):
        now = int(time.time()) if now is None else now
        policy = verified_policy(envelope, device_id, verification_key, now)
        sealed = self.protection.encrypt(canonical(envelope))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identity = self._read_identity(db)
            if (not identity or identity.get("erase_pending") or identity["device_id"] != device_id
                    or identity["verification_key"] != verification_key):
                raise ValueError("Chính sách thuộc một lần ghép đôi đã kết thúc.")
            changed = db.execute("""
                INSERT INTO cached_policy VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                version=excluded.version, data=excluded.data WHERE cached_policy.version <= excluded.version
            """, (device_id, policy["version"], sealed))
            if changed.rowcount != 1:
                raise ValueError("Từ chối phiên bản chính sách cũ.")
        return policy

    def read_policy(self, device_id: str, verification_key: str, now: int | None = None) -> dict | None:
        now = int(time.time()) if now is None else now
        with self.connection() as db:
            row = db.execute("SELECT data FROM cached_policy WHERE device_id=?", (device_id,)).fetchone()
        if row is None:
            return None
        try:
            raw = self.protection.decrypt(row[0])
            return verified_policy(json.loads(raw), device_id, verification_key, now)
        except (ValueError, OSError):
            return None

    def enqueue(self, payload: dict, *, now: int | None = None):
        event = UsagePayload.model_validate(payload)
        now = int(time.time()) if now is None else now
        if not now - 90 * 86400 <= event.ts <= now + 120:
            raise ValueError("Sự kiện ngoài khoảng lưu trữ.")
        item = UsageEnvelope(event_id=uuid4(), payload=event).model_dump(mode="json")
        sealed = self.protection.encrypt(canonical(item))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identity = self._read_identity(db)
            if (not identity or identity.get("erase_pending") or identity["device_id"] != str(event.device_id)
                    or identity["child_id"] != event.child_id):
                raise ValueError("Thiết bị chưa ghép đôi đúng hồ sơ.")
            db.execute("DELETE FROM outbox WHERE ts < ?", (now - 90 * 86400,))
            db.execute("INSERT INTO outbox VALUES (?,?,?)", (item["event_id"], event.ts, sealed))
            db.execute("DELETE FROM outbox WHERE rowid IN (SELECT rowid FROM outbox ORDER BY ts,rowid "
                       "LIMIT MAX(0,(SELECT count(*) FROM outbox)-10000))")
        return item["event_id"]

    def batch(self, *, now: int | None = None, enrollment: dict | None = None) -> list[dict]:
        """Revalidate protected records against the enrollment actually making this request."""
        now = int(time.time()) if now is None else now
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            identity = self._read_identity(db)
            if not identity or identity.get("erase_pending"):
                return []
            if enrollment is not None and not same_enrollment(identity, enrollment):
                raise ValueError("Lần ghép máy đã đổi. Cần bắt đầu nhịp đồng bộ mới.")
            db.execute("DELETE FROM outbox WHERE ts < ?", (now - 90 * 86400,))
            rows = db.execute("""
                SELECT event_id, ts, data FROM outbox
                WHERE ts <= ? AND NOT EXISTS (
                    SELECT 1 FROM outbox_quarantine q WHERE q.event_id = outbox.event_id
                ) ORDER BY ts,rowid LIMIT 100
            """, (now + 120,)).fetchall()
            batch, quarantined = [], []
            for event_id, timestamp, sealed in rows:
                try:
                    item = UsageEnvelope.model_validate_json(self.protection.decrypt(sealed))
                    valid = (str(item.event_id) == event_id and item.payload.ts == timestamp
                             and str(item.payload.device_id) == identity["device_id"]
                             and item.payload.child_id == identity["child_id"])
                except ValueError:
                    valid = False
                if valid:
                    batch.append(item.model_dump(mode="json"))
                else:
                    quarantined.append((event_id,))
            if quarantined:
                db.executemany("INSERT INTO outbox_quarantine VALUES (?)", quarantined)
                logging.getLogger(__name__).warning("Đã cách ly %d sự kiện cục bộ không xác minh được.", len(quarantined))
        return batch

    def acknowledge(self, ids: list[str]):
        with self.connection() as db:
            db.executemany("DELETE FROM outbox WHERE event_id=?", [(i,) for i in ids])

    def erase(self, identity: dict, *, retain_ack: bool):
        """Remove cached child data and retain only a sealed acknowledgement if requested."""
        receipt = {k: identity[k] for k in ("device_id", "erasure_token", "origin")}
        receipt["erase_pending"] = True
        # Only the committed local wipe shares a lock with the sampler. The server
        # request and the subsequent receipt acknowledgement can each take seconds.
        with self.activity_lock:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._read_identity(db)
                if current is None or current["device_id"] != identity["device_id"]:
                    return
                db.execute("DELETE FROM enrollment")
                db.execute("DELETE FROM cached_policy")
                db.execute("DELETE FROM outbox")
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ("screen_clock", "applied_time_commands"):
                    if table in tables:
                        db.execute(f"DELETE FROM {table} WHERE device_id=?", (identity["device_id"],))
                if retain_ack:
                    db.execute("INSERT INTO enrollment VALUES(1,?)", (self.protection.encrypt(canonical(receipt)),))
            listeners, self._erasure_listeners = self._erasure_listeners, []
            for ref in listeners:
                callback = ref()
                if callback is not None:
                    callback()
