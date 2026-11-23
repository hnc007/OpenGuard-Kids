import base64
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.coordinator import DemoSession
from agent.screen_time import CounterStore
from agent.store import AgentStore
from agent.tray import Visibility
from shared.protocol import PolicySettings, canonical
from test_family import home as paired_family


class TestProtection:
    """No credentials in the fixture; exercise the same signed policy/store/queue path."""
    encrypt = staticmethod(bytes)
    decrypt = staticmethod(bytes)


@pytest.fixture
def fixture(tmp_path):
    env = SimpleNamespace(wall=1_800_000_000, mono=2000, visible=True, notices=[])
    db = AgentStore(tmp_path / "paired.db", protection=TestProtection())
    key = Ed25519PrivateKey.generate()
    origin = "http://127.0.0.1:8000"
    pub = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    env.identity = dict(device_id=str(uuid4()), child_id=4, origin=origin, verification_key=pub,
                        refresh_token="fixture", erasure_token="fixture-delete")
    db.save_identity(env.identity)
    body = PolicySettings(weekday_minutes=3, weekend_minutes=3).model_dump()
    env.policy = body | dict(device_id=env.identity["device_id"], child_id=4, issuer="Phụ huynh",
                             version=1, issued_at=env.wall - 18000, expires_at=env.wall + 7 * 86400)
    signature = key.sign(canonical(env.policy))
    db.save_policy(dict(policy=env.policy, signature=base64.b64encode(signature).decode()),
                   env.identity["device_id"], pub, now=env.wall)
    env.client = SimpleNamespace(store=db, transport=SimpleNamespace(origin=origin))
    env.start = lambda: DemoSession(env.client, lambda: env.visible, env.notices.append,
                                    lambda: env.wall, lambda: env.mono)
    return env


def test_ui_lease_expires_and_hidden_clears_immediately():
    t = [12]
    visibility = Visibility(lambda: t[0])
    assert not visibility()
    visibility.update(True)
    assert visibility()
    t[0] += 2
    assert not visibility()
    visibility.update(True)
    visibility.update(False)
    assert not visibility()


def test_paired_session_to_outbox_and_remote_commands(fixture):
    env, session = fixture, fixture.start()
    for _ in range(32):
        session.tick()
        env.mono += 1
    assert "Phụ huynh" in session.try_domain()
    command = {"id": str(uuid4()), "action": "add_15", "expires_at": env.wall + 150}
    session.command(command)
    session.command(command)
    assert session.clock.state.extra_seconds == 900
    events = [entry["payload"] for entry in env.client.store.batch(now=env.wall)]
    assert [(row["type"], row["subject"]) for row in events] == [
        ("app_stop", "openguard-demo.exe"), ("blocked_domain", "example.org")]
    env.visible = False
    with pytest.raises(ValueError):
        session.command(command | {"id": str(uuid4())})
    before = session.clock.state.used
    session.tick()
    assert session.clock.state.used == before


def test_restart_with_rollback_requires_heartbeat_then_offline_expiry(fixture):
    env, session = fixture, fixture.start()
    for _ in range(8):
        session.tick()
        env.mono += 1
    env.wall -= 3 * 3600
    restarted = env.start()
    assert not restarted.ready() and restarted.needs_reference
    def sync(status, used, apply, *, on_policy_ready, **snapshot):
        timestamp = env.wall + 3 * 3600 + 8
        on_policy_ready(timestamp)
        return {"server_time": timestamp}
    env.client.sync_once = sync
    restarted.sync()
    assert restarted.ready()
    for _ in range(32):
        restarted.tick()
        env.mono += 1
    events = env.client.store.batch(now=int(restarted.now()))
    assert events[0]["payload"]["ts"] > env.wall + 1800
    env.mono += 7 * 86400
    assert not restarted.ready()


def test_confirmed_deletion_clears_memory_and_future_writes(fixture):
    env, session = fixture, fixture.start()
    session.tick()
    env.mono += 5
    session.tick()
    def erase(status, used, apply, **snapshot):
        env.client.store.erase(env.identity, retain_ack=False)
        return {"erased": True}
    env.client.sync_once = erase
    session.sync()
    env.mono += 2
    session.tick()
    session.close()
    assert session.erased and not session.identity and not session.policy
    assert CounterStore(env.client.store.path, env.identity["device_id"]).load().day == ""
    assert not env.client.store.batch(now=env.wall)


def test_explicit_lab_input_is_sampled_only_in_visible_valid_session(fixture):
    env = fixture
    calls = []
    state = [0, True]
    def sample():
        calls.append(env.mono)
        return tuple(state)
    session = DemoSession(env.client, lambda: env.visible, env.notices.append,
                          lambda: env.wall, lambda: env.mono, input_sample=sample)
    env.visible = False
    session.tick()
    assert not calls
    env.visible = True
    session.tick()
    assert calls and not session.available
    state[:] = [600, False]
    for _ in range(3):
        env.mono += 1
        session.tick()
    assert session.clock.state.used == 0
    state[0] = 0
    for _ in range(3):
        env.mono += 1
        session.tick()
    assert session.clock.state.used == 2
    sent = []
    def sync(status, used, apply, *, remaining_seconds, on_policy_ready):
        sent.append((status, remaining_seconds))
        on_policy_ready(int(session.now()))
        return {"server_time": int(session.now())}
    env.client.sync_once = sync
    session.sync()
    env.visible = False
    session.sync()
    assert sent == [("visible", 178), ("paused", None)]


def test_win32_idle_tick_rollover():
    from agent.lab_input import idle_duration
    assert idle_duration(500, 0xFFFFFFFF - 499) == 1


def test_rest_window_uses_fresh_lab_reading_and_releases_on_unlock_or_scope_loss(fixture):
    env = fixture
    class Console:
        active = True
        def __call__(self):
            return 0, not self.active
        def console_available(self):
            return self.active
    console = Console()
    session = DemoSession(env.client, lambda: env.visible, env.notices.append,
                          lambda: env.wall, lambda: env.mono, input_sample=console)
    session.tick()
    assert session.current_lab_rest() is None
    command = {"action": "lock", "id": str(uuid4()), "expires_at": env.wall + 60}
    session.command(command)
    note = session.current_lab_rest()
    assert note.reasons == ("parent",) and "Phụ huynh" in note.explanation()
    console.active = False
    assert session.current_lab_rest() is None
    console.active = True
    env.visible = False
    assert session.current_lab_rest() is None
    env.visible = True
    env.mono += 3
    assert session.current_lab_rest() is None
    session.tick()
    assert session.current_lab_rest().episode == note.episode
    session.command(command | {"id": str(uuid4()), "action": "unlock"})
    assert session.current_lab_rest() is None
    session.command(command | {"id": str(uuid4())})
    assert session.current_lab_rest().episode > note.episode
    env.mono += 8 * 86400
    assert session.current_lab_rest() is None


def test_rest_window_dismissal_survives_updates_until_next_break():
    from agent.tray import RestWindows

    drawn = []
    class View:
        def __init__(self, dismiss):
            self.dismiss = dismiss
        def refresh(self, notice):
            drawn.append(("render", notice.episode))
        def destroy(self):
            drawn.append(("release", None))
    windows = RestWindows(View)
    first = SimpleNamespace(episode=1)
    windows.update(first)
    windows.window.dismiss()
    windows.update(None)
    windows.update(first)
    assert drawn == [("render", 1), ("release", None)]
    windows.update(SimpleNamespace(episode=2))
    windows.update(None)
    assert drawn[-2:] == [("render", 2), ("release", None)]


def test_rest_dialog_help_requests_and_escape_callbacks_do_not_need_desktop(monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    import agent.tray as tray

    callbacks, targets = {}, []
    class Widget:
        def __init__(self, *args, **kwargs):
            if "command" in kwargs:
                callbacks[kwargs["text"]] = kwargs["command"]
        def pack(self, **kwargs):
            return self
        def title(self, text):
            self.caption = text
        def protocol(self, name, callback):
            callbacks[name] = callback
        def bind(self, name, callback):
            callbacks[name] = callback
    for namespace, names in ((tk, ("Toplevel", "StringVar")), (ttk, ("Frame", "Label", "Button"))):
        for name in names:
            monkeypatch.setattr(namespace, name, Widget)
    monkeypatch.setattr(tray.webbrowser, "open", targets.append)
    dismissals = []
    child = {"id": 17}
    tray.RestDialog(object(), lambda: dismissals.append(True),
                   lambda page: tray.open_child_page("http://127.0.0.1:8000", page, child.get("id")))
    assert not targets
    callbacks["Xin thêm giờ trong Góc của em"]()
    callbacks["Mở trang Tổng đài 111"]()
    callbacks["<Escape>"](None)
    callbacks["WM_DELETE_WINDOW"]()
    assert targets == ["http://127.0.0.1:8000/#child-time/17", "https://tongdai111.vn/"]
    child.clear()
    callbacks["Xin thêm giờ trong Góc của em"]()
    assert len(targets) == 2
    assert dismissals == [True, True]


def test_tray_links_only_expose_numeric_enrolled_child_in_fragment(monkeypatch):
    from urllib.parse import urlsplit
    import agent.tray as tray

    targets = []
    monkeypatch.setattr(tray.webbrowser, "open", targets.append)
    for page in ("time", "appeal", "report", "privacy"):
        tray.open_child_page("https://example.org", page, 23)
        target = urlsplit(targets[-1])
        assert (target.path, target.query, target.fragment) == ("/", "", f"child-{page}/23")
    for invalid in (None, True, 0, -1, 2**53, "23", "23?token=invalid"):
        assert not tray.open_child_page("https://example.org", "time", invalid)
    assert not tray.open_child_page("https://example.org", "arbitrary", 23)
    assert len(targets) == 4


def test_paired_lab_dns_pauses_without_console_or_explanation_and_omits_help(fixture):
    import dns.message
    import dns.rcode
    from agent.coordinator import LabDns
    from agent.dns_proxy import Resolver
    from agent.simulate import example_upstream

    env, session = fixture, fixture.start()
    can_show, console = [True], [True]
    gate = LabDns(session, lambda: console[0], lambda text: can_show[0])
    resolver = Resolver(gate, gate.scope, example_upstream, gate.observe, on_allowed=gate.observe)
    def ask(name):
        return resolver.answer(dns.message.make_query(name, "A")).rcode()
    assert ask("private.north-forum.example.org") == dns.rcode.NXDOMAIN
    assert ask("contact.tongdai111.vn") == dns.rcode.NOERROR
    can_show[0] = False
    assert ask("north-forum.example.org") == dns.rcode.NOERROR
    console[0] = False
    assert ask("north-forum.example.org") == dns.rcode.NOERROR
    events = env.client.store.batch(now=env.wall)
    assert [(row["payload"]["subject"], row["payload"]["type"]) for row in events] == [
        ("example.org", "blocked_domain")]


def test_lab_app_is_explained_revalidated_and_records_only_confirmed_exit():
    import threading
    from agent.lab_app import AppTrial
    from agent.policy import Application

    sid, digest = "S-1-5-21-1-2-3-4444", "a" * 64
    rule = {"name": "notepad.exe", "sha256": digest}
    events, posts, validations = [], [], []
    policy = {"issuer": "Phụ huynh", "blocked_apps": [rule], "allowed_apps": []}
    session = SimpleNamespace(ready=lambda: True, load_policy=lambda: validations.append(1), policy=policy,
                              lock=threading.RLock(), event=lambda *item: events.append(item))
    lab = SimpleNamespace(sid=sid, console_available=lambda: True)
    process = SimpleNamespace(snapshot=lambda _: Application("notepad.exe", digest, sid), exited=lambda: False)
    def request(allowed):
        if allowed():
            posts.append(True)
            return True
        return False
    process.ask_to_close = request
    trial = AppTrial(session, lab, process)
    matched = trial.blocked()
    assert matched and not trial.finish(None)
    policy["blocked_apps"] = []
    assert not trial.finish(matched[1])
    policy["blocked_apps"] = [rule]
    process.snapshot = lambda _: Application("notepad.exe", digest, "other")
    assert not trial.finish(matched[1])
    process.snapshot = lambda _: Application("notepad.exe", digest, sid)
    assert trial.finish(matched[1]) and posts == [True]
    assert len(validations) >= 5
    assert not trial.confirmed() and not events
    process.exited = lambda: True
    assert trial.confirmed() and not trial.confirmed()
    assert events == [("blocked_app", "notepad.exe")]


class FixtureForeground:
    sid = "S-1-5-21-11-22-33-1001"

    def __init__(self):
        self.name, self.owner = "lesson.exe", self.sid
        self.idle, self.locked, self.samples = 0, False, 0

    def __call__(self):
        return self.idle, self.locked

    def console_available(self):
        return not self.locked

    def reset_foreground(self):
        self.last_hash = None

    def foreground(self, permitted, *, needs_hash):
        from agent.policy import Application
        assert permitted()
        self.samples += 1
        return Application(self.name, "a" * 64 if needs_hash else "", self.owner) if self.name else None


class FixtureConsent:
    def __init__(self):
        self.enabled, self.generation = True, 1

    def __call__(self):
        return self.enabled


def test_foreground_switch_idle_private_programs_and_minimal_payload(fixture):
    env = fixture
    source, consent = FixtureForeground(), FixtureConsent()
    session = DemoSession(env.client, lambda: env.visible, env.notices.append, lambda: env.wall, lambda: env.mono,
                          input_sample=source, foreground_consent=consent)
    def tick():
        session.tick()
        env.mono += 1
    for _ in range(6):
        tick()
    source.name = "math.exe"
    for _ in range(5):
        tick()
    source.idle = 301
    tick()
    assert source.samples == 11
    source.idle = 0
    for subject in ["explorer.exe", "msedge.exe", "phoneexperiencehost.exe", r"C:\user\doc.exe"]:
        source.name = subject
        tick()
        tick()
    source.owner, source.name = "other", "private.exe"
    tick()
    tick()
    session.close()
    rows = [e["payload"] for e in env.client.store.batch(now=env.wall)]
    assert [(r["subject"], r["duration_sec"], r["ts"] - env.wall) for r in rows] == [
        ("lesson.exe", 5, 5), ("math.exe", 4, 10)]
    assert all(set(r) == {"ts", "child_id", "device_id", "type", "policy_id", "subject", "duration_sec"} for r in rows)


def test_foreground_never_reads_after_withdrawal_lock_or_loss_of_visibility(fixture):
    source, consent = FixtureForeground(), FixtureConsent()
    env = fixture
    session = DemoSession(env.client, lambda: env.visible, env.notices.append, lambda: env.wall, lambda: env.mono,
                          input_sample=source, foreground_consent=consent)
    for _ in range(3):
        session.tick()
        env.mono += 1
    consent.enabled = False
    consent.generation += 1
    session.tick()
    assert source.samples == 3 and session.foreground.name is None
    consent.enabled, env.visible = True, False
    session.tick()
    env.visible, source.locked = True, True
    session.tick()
    assert source.samples == 3
    source.locked = False
    session.tick()
    env.mono += 1200  # resume with the same window after a long sample gap
    session.tick()
    session.close()
    assert not env.client.store.batch(now=env.wall)


def test_slow_offline_sync_keeps_foreground_and_quota_ticks_and_serializes_retry(fixture):
    env, source = fixture, FixtureForeground()
    session = DemoSession(env.client, lambda: env.visible, env.notices.append, lambda: env.wall, lambda: env.mono,
                          input_sample=source, foreground_consent=FixtureConsent())
    entered, release, retry_started = Event(), Event(), Event()
    snapshots = []

    def sync(status, used, apply, *, remaining_seconds, on_policy_ready):
        snapshots.append((status, used, remaining_seconds))
        if len(snapshots) == 1:
            entered.set()
            assert release.wait(5)
            raise TimeoutError("synthetic offline server")
        on_policy_ready(session.now())
        return {}

    def retry():
        retry_started.set()
        session.sync()

    def sample():
        for _ in range(35):
            env.mono += 1
            session.tick()

    env.client.sync_once = sync
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(session.sync)
        try:
            assert entered.wait(3)
            queued = pool.submit(retry)
            assert retry_started.wait(3)
            pool.submit(sample).result(timeout=3)
            assert snapshots == [("visible", 0, 180)]
            assert session.clock.state.used == session.counter.load().used == 35
            assert [(e["payload"]["subject"], e["payload"]["duration_sec"])
                    for e in env.client.store.batch(now=env.wall + 35)] == [("lesson.exe", 30)]
        finally:
            release.set()
        with pytest.raises(TimeoutError, match="synthetic offline"):
            first.result(timeout=3)
        queued.result(timeout=3)
    session.close()
    assert snapshots[-1] == ("visible", 35, 145)
    assert [e["payload"]["duration_sec"] for e in env.client.store.batch(now=env.wall + 35)] == [30, 5]


@pytest.mark.parametrize("cause", ["erasure", "revoked_refresh"])
def test_lost_wipe_receipt_releases_sampler_and_rejects_stale_writes(fixture, cause):
    from agent.client import AgentClient, DeviceHTTPError

    env, source = fixture, FixtureForeground()
    store = env.client.store
    identity = env.identity | {"access_token": "fixture-access", "access_until": 0}
    store.save_identity(identity, previous=env.identity)
    client = AgentClient(store, identity["origin"])
    acknowledgments = []
    waiting, release = Event(), Event()

    def request(method, path, data=None, token=None):
        if path.endswith("/refresh") and cause == "revoked_refresh":
            raise DeviceHTTPError(401)
        assert method == "POST" and path.endswith("/erasure")
        if not data["acknowledge"] and cause == "revoked_refresh":
            raise DeviceHTTPError(404)
        if data["acknowledge"]:
            acknowledgments.append(data)
            if len(acknowledgments) == 1:
                waiting.set()
                assert release.wait(5)
                raise TimeoutError("lost erasure receipt")
        return {"erase": True}

    client.transport.request = request
    session = DemoSession(client, lambda: env.visible, env.notices.append, lambda: env.wall, lambda: env.mono,
                          input_sample=source, foreground_consent=FixtureConsent())
    command = {"id": str(uuid4()), "action": "add_15", "expires_at": env.wall + 600}
    session.command(command)
    for _ in range(6):
        env.mono += 1
        session.tick()
    session.event("domain_query", "example.org")
    stale_counter, stale_state = session.counter, session.clock.state

    def assert_wiped():
        with store.connection() as db:
            for table in ("screen_clock", "applied_time_commands", "outbox", "cached_policy"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        with pytest.raises(ValueError, match="ghép máy"):
            stale_counter.save(stale_state)
        with pytest.raises(ValueError, match="ghép máy"):
            stale_counter.apply_simulation(command, env.wall)

    with ThreadPoolExecutor(max_workers=2) as pool:
        inflight = pool.submit(session.sync)
        try:
            assert waiting.wait(3) and store.identity()["erase_pending"]
            observed = source.samples
            assert session.erased and not session.ready() and session.counter is None
            assert not session.identity and not session.policy and session.clock.state.used == 0
            env.mono += 6
            pool.submit(session.tick).result(timeout=3)
            session.accept_heartbeat(env.wall)
            with pytest.raises(ValueError, match="tạm dừng"):
                session.command(command)
            session.close()
            assert source.samples == observed and session.policy is None and not session.identity
            assert_wiped()
        finally:
            release.set()
        with pytest.raises(TimeoutError, match="lost erasure receipt"):
            inflight.result(timeout=3)
    assert store.identity()["erase_pending"] and session.erased
    assert client.retry_pending_receipt() == {"erased": True}
    assert len(acknowledgments) == 2 and store.identity() is None
    assert_wiped()


def test_counter_binding_survives_token_refresh_and_rejects_a_different_enrollment(fixture):
    env, session = fixture, fixture.start()
    first_handle = session.counter
    second_store = AgentStore(env.client.store.path, TestProtection())
    refreshed = env.identity | {"refresh_token": "fixture-rotated"}
    second_store.save_identity(refreshed, previous=env.identity)
    session.tick()
    for _ in range(6):
        env.mono += 1
        session.tick()
    assert first_handle.load().used == 5
    second_store.erase(refreshed, retain_ack=False)
    second_store.save_identity(refreshed | {"device_id": str(uuid4())}, new=True)
    with pytest.raises(ValueError, match="ghép máy"):
        first_handle.save(session.clock.state)
    session.tick()
    assert session.erased and first_handle.load().day == ""


def test_foreground_decision_advises_once_without_reporting_a_block(fixture):
    env, source, consent = fixture, FixtureForeground(), FixtureConsent()
    session = DemoSession(env.client, lambda: env.visible, env.notices.append, lambda: env.wall, lambda: env.mono,
                          input_sample=source, foreground_consent=consent)
    session.policy = session.policy | {"blocked_apps": [{"name": "old-name.exe", "sha256": "a" * 64}]}
    for _ in range(3):
        session.tick()
        env.mono += 1
    session.close()
    assert len(env.notices) == 1 and "lesson.exe" in env.notices[0] and "Phụ huynh" in env.notices[0]
    assert [item["payload"]["type"] for item in env.client.store.batch(now=env.wall)] == ["app_stop"]


@pytest.mark.skipif(os.name != "nt", reason="Mocked Windows ABI")
def test_native_foreground_validates_owner_session_and_visibility_before_image_lookup(monkeypatch):
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32security
    from agent import lab_input

    lab = lab_input.LabInput.__new__(lab_input.LabInput)
    lab.sid, lab.session = "child-sid", 7
    lab.console_available = lambda: True
    owner, current, seen, rights = ["adult-sid"], [7], [], []
    handle = SimpleNamespace(Close=lambda: None)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 99)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (222, 333))
    def open_process(flags, inherit, pid):
        rights.append((flags, inherit, pid))
        return handle
    monkeypatch.setattr(win32api, "OpenProcess", open_process)
    monkeypatch.setattr(win32security, "OpenProcessToken", lambda proc, flags: handle)
    monkeypatch.setattr(win32security, "GetTokenInformation",
                        lambda token, kind: (owner[0], 0) if kind == win32security.TokenUser else current[0])
    monkeypatch.setattr(win32security, "ConvertSidToStringSid", lambda sid: sid)
    def path(_):
        seen.append(True)
        return r"C:\Users\lab-test\Private-folder\Lesson.exe"
    monkeypatch.setattr(lab_input, "image_path", path)
    assert lab.foreground(lambda: False, needs_hash=False) is None and not rights
    assert lab.foreground(lambda: True, needs_hash=False) is None
    owner[0], current[0] = lab.sid, 8
    assert lab.foreground(lambda: True, needs_hash=False) is None and not seen
    current[0] = lab.session
    app = lab.foreground(lambda: True, needs_hash=False)
    assert app.name == "lesson.exe" and app.owner_sid == lab.sid and not app.sha256
    assert all(right == (win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, 333) for right in rights)


@pytest.mark.skipif(os.name != "nt", reason="Mocked Windows drive type")
def test_lab_image_hash_read_is_bounded_cancellable_and_forgets_cached_file(tmp_path, monkeypatch):
    import hashlib
    import win32file
    from agent.lab_input import LabInput
    lab = LabInput.__new__(LabInput)
    lab.reset_foreground()
    image = tmp_path / "fixture.exe"
    data = b"MZ" + b"synthetic-data" * 120000
    image.write_bytes(data)
    monkeypatch.setattr(win32file, "GetDriveType", lambda *_: win32file.DRIVE_FIXED)
    permissions = iter([True, True, False, False])
    assert not lab._image_hash(str(image), lambda: next(permissions, False))
    assert lab.last_file is None
    assert lab._image_hash(str(image), lambda: True) == hashlib.sha256(data).hexdigest()
    assert all(isinstance(field, int) for field in lab.last_file)
    lab.reset_foreground()
    assert lab.last_hash is None and lab.last_file is None


def test_foreground_event_retry_reaches_both_family_reports_once(paired_family, tmp_path):
    import time
    from datetime import datetime, timedelta
    from agent.client import AgentClient
    from server.family import ZONE
    from test_sync import InProcessTransport

    _, parent, child, _, _, prefix, identity = paired_family
    device = AgentStore(tmp_path / "foreground-demo.sqlite3", TestProtection())
    device.save_identity(identity | {"origin": InProcessTransport.origin})
    agent = AgentClient(device, InProcessTransport.origin)
    agent.transport = InProcessTransport(parent)
    agent.sync_once()
    # Timestamp samples before the report's current moment. No app or window APIs run.
    base = int(time.time()) - 40
    mono = [0]
    source = FixtureForeground()
    session = DemoSession(agent, lambda: True, lambda _: True, lambda: base + mono[0], lambda: mono[0],
                          input_sample=source, foreground_consent=FixtureConsent())
    for i in range(32):
        mono[0] = i
        session.tick()
    session.close()
    assert [row["payload"]["duration_sec"] for row in device.batch()] == [30, 1]
    agent.transport.lose_ack = True
    with pytest.raises(ConnectionError):
        agent.sync_once()
    agent.sync_once()
    assert device.batch() == []
    reports = parent.get(prefix + "/reports").json()
    assert reports == child.get(prefix + "/reports").json()
    assert sum(day["seconds"] for day in reports["days"]) == 31
    # Top-app ranking covers the current week, which can begin inside the test interval.
    now = datetime.now(ZONE)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    in_week = max(0, base + 31 - max(base, int(monday.timestamp())))
    assert reports["apps"] == ([["lesson.exe", in_week]] if in_week else [])
