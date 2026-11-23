"""Screen-time accounting for one device, sampled in its child's interactive session."""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.policy import Scope

VIETNAM = timezone(timedelta(hours=7))
IDLE_SECONDS = 300
MAX_SAMPLE_GAP = 5
CLOCK_ALERT_SECONDS = 90


@dataclass
class ClockState:
    day: str = ""
    used: float = 0
    logical_ts: float = 0
    warned: int = 0
    clock_alerts: int = 0
    skewed: bool = False
    extra_seconds: int = 0
    parent_locked: bool = False


@dataclass(frozen=True)
class Reading:
    remaining_seconds: int
    action: str
    warnings: tuple[int, ...]
    clock_alert: bool
    rest_reasons: tuple[str, ...] = ()
    next_slot: datetime | None = None


def next_allowed_slot(schedule: list[str], local: datetime) -> datetime | None:
    """First enabled half-hour after the current (disabled) slot in the weekly calendar."""
    current_slot = local.weekday() * 48 + local.hour * 2 + local.minute // 30
    start = local.replace(minute=30 * (local.minute // 30), second=0, microsecond=0)
    for offset in range(1, 7 * 48):
        day, slot = divmod((current_slot + offset) % (7 * 48), 48)
        if schedule[day][slot] == "1":
            return start + timedelta(minutes=offset * 30)
    return None


class ScreenClock:
    def __init__(self, state: ClockState | None = None):
        self.state = state or ClockState()
        self.previous_mono: float | None = None
        self.previous_active = False

    def sample(self, policy: dict, *, monotonic: float, wall: float, scope: Scope,
               idle_seconds: float = 0, locked: bool = False,
               server_time: float | None = None) -> Reading:
        s = self.state
        first_sample = self.previous_mono is None
        elapsed = 0 if first_sample else max(0.0, monotonic - self.previous_mono)
        self.previous_mono = monotonic
        if not scope.active:
            self.previous_mono = None
            self.previous_active = False
            return Reading(0, "paused", (), False)

        # Across a restart use the persisted lower bound. During one run a change
        # to the PC's civil clock cannot select a more favourable day or time slot.
        if server_time is not None:
            logical = max(s.logical_ts, server_time)
        elif first_sample or not s.logical_ts:
            logical = max(s.logical_ts, wall)
        else:
            logical = s.logical_ts + elapsed
        skewed = abs(wall - logical) > CLOCK_ALERT_SECONDS
        alert = skewed and not s.skewed
        s.clock_alerts += int(alert)
        s.skewed = skewed
        s.logical_ts = logical
        local = datetime.fromtimestamp(logical, VIETNAM)
        day = local.date().isoformat()
        if day != s.day:
            s.day, s.used, s.warned, s.extra_seconds = day, 0, 0, 0
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            day_elapsed = max(0, logical - midnight.timestamp())
        else:
            day_elapsed = elapsed

        # The first sample after resume/reboot/unlock/switch establishes a baseline.
        if self.previous_active and not locked and elapsed <= MAX_SAMPLE_GAP:
            billable = min(elapsed, day_elapsed)
            s.used += max(0, billable - max(0, idle_seconds - IDLE_SECONDS))
        active = not locked and idle_seconds <= IDLE_SECONDS
        self.previous_active = active
        key = "weekend_minutes" if local.weekday() >= 5 else "weekday_minutes"
        quota = policy[key] * 60 + s.extra_seconds
        remaining = max(0, math.ceil(quota - s.used))
        allowed = policy["schedule"][local.weekday()][local.hour * 2 + local.minute // 30] == "1"
        reasons = []
        if s.parent_locked:
            reasons.append("parent")
        if not allowed:
            reasons.append("schedule")
        if remaining == 0:
            reasons.append("quota")
        if reasons:
            self.previous_active = False
            next_slot = None if allowed else next_allowed_slot(policy["schedule"], local)
            return Reading(remaining, "lock", (), alert, tuple(reasons), next_slot)
        warnings = []
        for bit, seconds in enumerate((600, 300, 60)):
            mask = 1 << bit
            if remaining <= seconds <= quota and not s.warned & mask:
                s.warned |= mask
                warnings.append(seconds)
        return Reading(remaining, "grace" if remaining <= 60 else "active", tuple(warnings), alert)


class CounterStore:
    """Single writer per device; checkpoints contain totals, never a timeline of user input."""
    def __init__(self, path: Path, device_id: str, *, write_transaction=None):
        self.path, self.device_id = path, device_id
        self.write_transaction = write_transaction or self._local_transaction
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS screen_clock (
                    device_id TEXT PRIMARY KEY, day TEXT, used REAL, logical_ts REAL, warned INTEGER,
                    clock_alerts INTEGER, skewed INTEGER, extra_seconds INTEGER, parent_locked INTEGER);
                CREATE TABLE IF NOT EXISTS applied_time_commands (
                    device_id TEXT, command_id TEXT, expires_at INTEGER,
                    PRIMARY KEY(device_id, command_id));
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            yield connection

    @contextmanager
    def _local_transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def load(self) -> ClockState:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM screen_clock WHERE device_id=?", (self.device_id,)).fetchone()
        if not row:
            return ClockState()
        fields = dict(row)
        del fields["device_id"]
        return ClockState(**fields)

    def _write(self, db: sqlite3.Connection, state: ClockState) -> None:
        db.execute("""INSERT OR REPLACE INTO screen_clock
            (device_id, day, used, logical_ts, warned, clock_alerts, skewed, extra_seconds, parent_locked)
            VALUES (:device_id, :day, :used, :logical_ts, :warned, :clock_alerts, :skewed,
                    :extra_seconds, :parent_locked)""", {"device_id": self.device_id, **asdict(state)})

    def save(self, state: ClockState) -> None:
        with self.write_transaction() as db:
            self._write(db, state)

    def apply_simulation(self, command: dict, server_time: float | Callable[[], float]) -> None:
        self.apply_command(command, None, server_time)

    def apply_command(self, command: dict, clock: ScreenClock | None, now: float | Callable[[], float],
                      *, permitted: Callable[[], bool] | None = None) -> None:
        action = command["action"]
        if action not in {"lock", "unlock", "add_15"}:
            raise ValueError("Lệnh không hợp lệ.")
        with self.write_transaction() as db:
            if permitted is not None and not permitted():
                raise ValueError("Phiên đã tạm dừng trong lúc chờ ghi lệnh.")
            applied_at = now() if callable(now) else now
            if applied_at >= command["expires_at"]:
                raise ValueError("Lệnh đã hết hạn.")
            if clock is None:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM screen_clock WHERE device_id=?", (self.device_id,)).fetchone()
                fields = dict(row) if row else {}
                fields.pop("device_id", None)
                s = ClockState(**fields)
                s.logical_ts = max(s.logical_ts, applied_at)
                day = datetime.fromtimestamp(s.logical_ts, VIETNAM).date().isoformat()
                if day != s.day:
                    s.day, s.used, s.warned, s.extra_seconds = day, 0, 0, 0
            else:
                s = ClockState(**asdict(clock.state))
            db.execute("DELETE FROM applied_time_commands WHERE expires_at<=?", (applied_at,))
            inserted = db.execute("INSERT OR IGNORE INTO applied_time_commands VALUES(?,?,?)",
                                  (self.device_id, command["id"], command["expires_at"]))
            if inserted.rowcount != 1:
                return
            if action == "add_15":
                s.extra_seconds += 900
                s.warned = 0
            else:
                s.parent_locked = action == "lock"
            self._write(db, s)
        if clock is not None:
            clock.state = s
