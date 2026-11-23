"""Visible development session with separate, voluntary controls for a dedicated Windows lab."""
from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from agent.lab_input import PRIVATE_APPS
from agent.policy import Decision, DomainRules, Scope, app_decision
from agent.screen_time import CLOCK_ALERT_SECONDS, IDLE_SECONDS, MAX_SAMPLE_GAP, VIETNAM, ScreenClock
from agent.store import same_enrollment
from shared.protocol import executable


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


class ForegroundAccounting:
    """One contiguous, sampled foreground segment in memory. Reports receive only its basename."""
    def __init__(self, session, consent):
        self.session, self.consent = session, consent
        self.clear()

    def clear(self):
        self.name = self.digest = None
        self.seconds = 0.0
        self.last_tick = self.end = 0.0
        self.version = None
        self.generation = self.consent.generation

    def permitted(self):
        return self.consent() and self.session.ready()

    def suspend(self):
        self.session.input_sample.reset_foreground()
        self.clear()

    def flush(self):
        seconds = int(self.seconds)
        if (self.name and seconds and self.permitted() and self.generation == self.consent.generation
                and self.session.input_sample.console_available()):
            self.session.event("app_stop", self.name, seconds, at=int(self.end), version=self.version)
            self.seconds -= seconds

    def sample(self, charged, *, active):
        session = self.session
        if not self.permitted() or self.generation != self.consent.generation:
            self.suspend()
        if not self.permitted():
            return
        if not active:
            self.flush()
            self.clear()
            return
        policy = session.policy
        rules_configured = bool(policy["allowed_apps"] or policy["blocked_apps"])
        app = session.input_sample.foreground(self.permitted, needs_hash=rules_configured)
        if not self.permitted() or self.generation != self.consent.generation:
            self.suspend()
            return
        name = None
        if app and app.owner_sid == session.input_sample.sid and not app.protected:
            try:
                candidate = executable(app.name)
                if candidate not in PRIVATE_APPS:
                    name = candidate
            except ValueError:
                name = None
        tick, logical = session.mono(), session.clock.state.logical_ts
        contiguous = (app and name == self.name and app.sha256 == self.digest and self.version == policy["version"]
                      and 0 <= tick - self.last_tick <= MAX_SAMPLE_GAP
                      and abs((logical - self.end) - (tick - self.last_tick)) < 2)
        if not name or not contiguous:
            self.flush()
            self.clear()
            if not name:
                return
            self.name, self.digest, self.version = name, app.sha256, policy["version"]
            if rules_configured and app.sha256:
                decision = app_decision(policy, app, session.input_sample.sid, Scope(True, True, True))
                if decision.action == "block" and self.permitted():
                    session.notice(f"{name}: {decision.reason} Người đặt quy tắc: {decision.issuer}. "
                                   "Em có thể xem quy tắc cùng phụ huynh trong Góc của em. Đây là lời nhắc.")
        else:
            # At a switch or missing sample the interval has no defensible owner.
            self.seconds += charged
        self.last_tick, self.end = tick, logical
        if self.seconds >= 30:
            self.flush()


class DemoSession:
    def __init__(self, client, visible: Callable[[], bool], notice: Callable[[str], None],
                 wall=time.time, mono=time.monotonic, input_sample=None, foreground_consent=None):
        self.client, self.store = client, client.store
        self.visible, self.notice = visible, notice
        self.wall, self.mono = wall, mono
        self.input_sample = input_sample
        self.foreground = ForegroundAccounting(self, foreground_consent) if foreground_consent is not None else None
        self.available = False
        self.remaining_seconds = None
        self.lock = self.store.activity_lock
        self.sync_lock = threading.Lock()
        self.erased = False
        self.identity = self.store.identity()
        if (not self.identity or self.identity.get("erase_pending")
                or self.identity["origin"] != client.transport.origin):
            raise ValueError("Hãy ghép đôi một máy mô phỏng với máy chủ này trước.")
        self.counter = self.store.counter(self.identity)
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
        with self.lock:
            self.check_identity()
            if not self.erased:
                self.store.on_local_wipe(self.forgotten)

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

    def event(self, kind: str, subject="", duration=0, *, at=None, version=None):
        if not self.ready():
            return
        self.store.enqueue(dict(ts=int(self.now()) if at is None else at, child_id=self.identity["child_id"],
                           device_id=self.identity["device_id"], policy_id=self.policy["version"] if version is None else version,
                           type=kind, subject=subject, duration_sec=duration), now=int(self.now()))

    def tick(self):
        with self.lock:
            self.check_identity()
            if self.erased:
                return
            self.available = False
            if not self.ready():
                if self.foreground:
                    self.foreground.suspend()
                self.clock.previous_mono = None
                self.clock.previous_active = False
                self.previous_action = None
                self.rest_notice = None
                self.status = "Tạm dừng. Cần hiện khay, mở cửa sổ và có quy tắc còn hạn."
                return
            idle, locked = self.input_sample() if self.input_sample else (0, False)
            if locked:
                if self.foreground:
                    self.foreground.suspend()
                self.clock.previous_mono = None
                self.clock.previous_active = False
                self.rest_notice = None
                self.status = "Tạm dừng khi phiên Windows lab bị khóa hoặc đổi người dùng."
                return
            self.available = True
            old_used, old_day = self.clock.state.used, self.clock.state.day
            result = self.clock.sample(self.policy, monotonic=self.mono(), wall=self.wall(),
                                       server_time=self.now(), idle_seconds=idle, scope=Scope(True, True, True))
            charged = self.clock.state.used - (old_used if self.clock.state.day == old_day else 0)
            if self.foreground:
                self.foreground.sample(charged, active=idle <= IDLE_SECONDS and result.action != "lock")
            else:
                self.pending_seconds += charged
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
            if not self.foreground and (self.pending_seconds >= 30 or result.action == "lock"):
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
        if self.foreground:
            self.foreground.flush()
            self.foreground.clear()
            return
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
            def still_visible():
                return self.ready() and (self.input_sample is None or not self.input_sample()[1])

            self.counter.apply_command(command, self.clock, self.now, permitted=still_visible)
            # Replace the displayed reason/remaining time in the same command callback.
            self.tick()

    def sync(self):
        with self.sync_lock:
            with self.lock:
                self.tick()
                if self.erased:
                    return
                active = self.ready() and self.available
                used = min(86400, int(self.clock.state.used))
                remaining = self.remaining_seconds if active else None
            try:
                result = self.client.sync_once("visible" if active else "paused", used, self.command,
                                               remaining_seconds=remaining, on_policy_ready=self.accept_heartbeat)
            finally:
                # A revoked refresh or lost receipt may have committed a wipe before raising.
                self.check_identity()
            with self.lock:
                if result.get("erased"):
                    self.forgotten()
                elif not self.erased:
                    self.tick()

    def accept_heartbeat(self, server_time):
        with self.lock:
            self.check_identity()
            if not self.erased:
                self.base, self.anchor = max(server_time, self.clock.state.logical_ts), self.mono()
                self.needs_reference = False
                self.load_policy()

    def forgotten(self):
        if self.erased:
            return
        self.erased = True
        if self.foreground:
            self.foreground.suspend()
        self.available = False
        self.identity, self.policy = {}, None
        self.counter = None
        self.domain_rules = None
        self.pending_seconds = 0
        self.remaining_seconds = None
        self.rest_notice = None
        self.clock = ScreenClock()
        self.status = "Đã ngừng phiên của thiết bị."

    def check_identity(self):
        with self.lock:
            if self.erased or not same_enrollment(self.store.identity(), self.identity):
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
