import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.coordinator import DemoSession
from agent.screen_time import CounterStore
from agent.store import AgentStore
from agent.tray import Visibility
from shared.protocol import PolicySettings, canonical


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
    env.client.sync_once = lambda status, used, apply, **snapshot: {"server_time": env.wall + 3 * 3600 + 8}
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
    def sync(status, used, apply, *, remaining_seconds):
        sent.append((status, remaining_seconds))
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
    tray.RestDialog(object(), lambda: dismissals.append(True), "http://127.0.0.1:8000")
    assert not targets
    callbacks["Xin thêm giờ trong Góc của em"]()
    callbacks["Mở trang Tổng đài 111"]()
    callbacks["<Escape>"](None)
    callbacks["WM_DELETE_WINDOW"]()
    assert targets == ["http://127.0.0.1:8000/#requests", "https://tongdai111.vn/"]
    assert dismissals == [True, True]


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
