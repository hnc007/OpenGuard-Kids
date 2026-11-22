"""Visible development session. Only synthetic activity reaches the paired demo profile."""
from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from agent.policy import Decision, DomainRules, Scope
from agent.screen_time import CLOCK_ALERT_SECONDS, VIETNAM, CounterStore, ScreenClock


@dataclass(frozen=True)
class RestNotice:
    episode: int
    version: int
    measured_at: float
    local_time: datetime
    remaining: int
    issuer: str
    reasons: tuple[str, ...]
    next_slot: datetime | None

    def explanation(self) -> str:
        lines = []
        if "parent" in self.reasons:
            lines.append(f"{self.issuer} vừa chọn cho em nghỉ. Em có thể nhờ mở lại.")
        if "schedule" in self.reasons:
            lines.append(f"Đây là giờ nghỉ theo lịch của {self.issuer}.")
            if self.next_slot:
                lines.append(f"Khung giờ được dùng tiếp theo bắt đầu lúc {self.next_slot:%H:%M} ngày {self.next_slot:%d/%m}.")
            else:
                lines.append("Tuần này chưa có khung giờ được dùng. Em có thể nhờ xem lại lịch.")
        if "quota" in self.reasons:
            lines.append(f"Em đã dùng hết thời gian hôm nay theo quy tắc của {self.issuer}.")
        return " ".join(lines)


class DemoSession:
    def __init__(self, client, visible: Callable[[], bool], notice: Callable[[str], None],
                 wall=time.time, mono=time.monotonic, input_sample=None):
        self.client, self.store = client, client.store
        self.visible, self.notice = visible, notice
        self.wall, self.mono = wall, mono
        self.input_sample = input_sample
        self.available = False
        self.remaining_seconds = None
        self.lock = threading.RLock()
        self.identity = self.store.identity()
        if (not self.identity or self.identity.get("erase_pending")
                or self.identity["origin"] != client.transport.origin):
            raise ValueError("Hãy ghép đôi một máy mô phỏng với máy chủ này trước.")
        self.counter = CounterStore(self.store.path, self.identity["device_id"])
        self.clock = ScreenClock(self.counter.load())
        floor = self.clock.state.logical_ts
        self.needs_reference = self.wall() + CLOCK_ALERT_SECONDS < floor
        self.base = max(floor, self.wall())
        self.anchor = self.mono()
        self.policy = None
        self.load_policy()
        self.status = "Đang chờ bắt đầu mô phỏng."
        self.previous_action = None
        self.rest_episode = 0
        self.rest_notice: RestNotice | None = None
        self.saved = self.anchor
        self.pending_seconds = 0.0
        self.erased = False

    def now(self):
        return self.base + max(0, self.mono() - self.anchor)

    def load_policy(self):
        self.policy = self.store.read_policy(self.identity["device_id"], self.identity["verification_key"],
                                             now=int(self.now()))
        self.domain_rules = DomainRules(self.policy) if self.policy else None

    def ready(self):
        policy = self.policy
        return bool(policy and not self.erased and self.visible() and not self.needs_reference
                    and self.now() < policy["expires_at"])

    def event(self, kind: str, subject="", duration=0):
        if not self.ready():
            return
        self.store.enqueue(dict(ts=int(self.now()), child_id=self.identity["child_id"],
                           device_id=self.identity["device_id"], policy_id=self.policy["version"],
                           type=kind, subject=subject, duration_sec=duration), now=int(self.now()))

    def tick(self):
        with self.lock:
            self.available = False
            if not self.ready():
                self.clock.previous_mono = None
                self.clock.previous_active = False
                self.previous_action = None
                self.rest_notice = None
                self.status = "Tạm dừng. Cần hiện khay, mở cửa sổ và có quy tắc còn hạn."
                return
            idle, locked = self.input_sample() if self.input_sample else (0, False)
            if locked:
                self.clock.previous_mono = None
                self.clock.previous_active = False
                self.rest_notice = None
                self.status = "Tạm dừng khi phiên Windows lab bị khóa hoặc đổi người dùng."
                return
            self.available = True
            old_used, old_day = self.clock.state.used, self.clock.state.day
            result = self.clock.sample(self.policy, monotonic=self.mono(), wall=self.wall(),
                                       server_time=self.now(), idle_seconds=idle, scope=Scope(True, True, True))
            self.pending_seconds += self.clock.state.used - (old_used if self.clock.state.day == old_day else 0)
            minutes, seconds = divmod(result.remaining_seconds, 60)
            self.remaining_seconds = result.remaining_seconds
            self.status = f"Em còn {minutes} phút {seconds:02d} giây trong phiên mô phỏng."
            if result.action == "lock":
                if self.previous_action != "lock":
                    self.rest_episode += 1
                    self.event("locked")
                self.rest_notice = RestNotice(self.rest_episode, self.policy["version"], self.mono(),
                    datetime.fromtimestamp(self.clock.state.logical_ts, VIETNAM), result.remaining_seconds,
                    self.policy["issuer"], result.rest_reasons, result.next_slot)
                self.status = self.rest_notice.explanation()
                if self.previous_action != "lock":
                    self.notice(self.status + " Em có thể xin thêm giờ.")
            else:
                self.rest_notice = None
            for threshold in result.warnings:
                self.notice(f"Em còn {threshold // 60} phút. Em nhớ lưu bài nhé.")
                self.event("quota_warning")
            # Reports explicitly name the simulated application. Real Windows usage
            # is never observed in this development coordinator.
            if self.pending_seconds >= 30 or result.action == "lock":
                self.flush_segment()
            self.previous_action = result.action
            if self.mono() - self.saved >= 5 or result.warnings or result.clock_alert:
                self.counter.save(self.clock.state)
                self.saved = self.mono()

    def current_lab_rest(self) -> RestNotice | None:
        # UI reads an immutable snapshot without waiting behind a network request.
        # A stuck sample, expired signature or lost console releases the window.
        note = self.rest_notice
        policy = self.policy
        if (note and policy and self.ready() and self.input_sample and policy["version"] == note.version
                and self.mono() - note.measured_at <= 2.5 and self.input_sample.console_available()):
            return note
        return None

    def flush_segment(self):
        seconds = int(self.pending_seconds)
        if seconds > 0:
            self.event("app_stop", "openguard-demo.exe", seconds)
            self.pending_seconds -= seconds

    def try_domain(self):
        with self.lock:
            if not self.ready():
                return "Mở phiên mô phỏng trước khi thử quy tắc."
            name = "north-forum.example.org"
            decision = self.domain_rules.decide(name, Scope(True, True, True))
            self.event("blocked_domain" if decision.action == "block" else "domain_query", name)
            return decision.explain(name)

    def command(self, command):
        with self.lock:
            if not self.ready():
                raise ValueError("Phiên đang tạm dừng.")
            self.tick()
            if not self.available:
                raise ValueError("Phiên Windows không khả dụng.")
            self.counter.apply_command(command, self.clock, self.now())
            # Replace the displayed reason/remaining time in the same command callback.
            self.tick()

    def sync(self):
        with self.lock:
            # Serialize counter writes, the credential refresh and a possible local
            # wipe. No checkpoint can race the deletion acknowledgement.
            self.tick()
            active = self.ready() and self.available
            result = self.client.sync_once("visible" if active else "paused",
                                           min(86400, int(self.clock.state.used)), self.command,
                                           remaining_seconds=self.remaining_seconds if active else None)
            if result.get("erased"):
                self.forgotten()
                return
            self.base, self.anchor = max(result["server_time"], self.clock.state.logical_ts), self.mono()
            self.needs_reference = False
            self.load_policy()
            self.tick()

    def forgotten(self):
        self.erased = True
        self.identity, self.policy = {}, None
        self.domain_rules = None
        self.pending_seconds = 0
        self.remaining_seconds = None
        self.rest_notice = None
        self.clock = ScreenClock()
        self.status = "Đã ngừng phiên của thiết bị."

    def check_identity(self):
        with self.lock:
            identity = self.store.identity()
            if not identity or identity.get("erase_pending"):
                self.forgotten()

    def close(self):
        with self.lock:
            self.check_identity()
            if self.ready():
                self.flush_segment()
                self.counter.save(self.clock.state)


class LabDns:
    """Loopback proxy for a dedicated single-user VM, active only while its actual tray is visible."""
    def __init__(self, session: DemoSession, console_available, show_reason):
        self.session, self.console_available, self.show_reason = session, console_available, show_reason

    def scope(self):
        active = bool(self.session.ready()) and self.console_available()
        return Scope(active, active, active)

    def decide(self, name, scope):
        rules = self.session.domain_rules
        if not rules or not self.scope().active:
            return Decision("allow", "Phiên tạm dừng.")
        return rules.decide(name, scope)

    def observe(self, name, decision):
        from shared.protocol import reporting_domain
        from agent.policy import ancestors, safety_domains

        session = self.session
        if not self.scope().active or not session.lock.acquire(blocking=False):
            return False
        try:
            if not self.scope().active:
                return False
            try:
                subject = reporting_domain(name)
            except ValueError:
                subject = None
            if decision.action == "block":
                if not self.show_reason(decision.explain(subject or "Trang này")):
                    return False
            if subject and not safety_domains().intersection(ancestors(name.lower())):
                session.event("blocked_domain" if decision.action == "block" else "domain_query", subject)
            return True
        except (OSError, sqlite3.Error, ValueError):
            return False
        finally:
            session.lock.release()
