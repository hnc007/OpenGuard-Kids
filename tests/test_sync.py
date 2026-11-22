import base64
import heapq
import os
import threading
import time
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event, func, select, text, update

from agent.client import AgentClient, DeviceHTTPError, Transport, erasure_message
from agent.store import AgentStore
from server.cli import create_user
from server.db import ROOT, make_engine, migrate
from server.main import create_app
from server.family import prune_expired
from server.family_models import Command, Erasure
from server.models import Child
from server.security import digest
from server.sync_models import Device, PairCode, UsageEvent
from shared.protocol import PolicySettings, UsagePayload, canonical, reporting_domain, verified_policy

CONSENT = {"consent": True, "child_informed": True, "child_agrees": True}


@pytest.fixture
def family(tmp_path):
    url = f"sqlite:///{(tmp_path / 'server.sqlite3').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=base64.urlsafe_b64encode(os.urandom(32)).decode())
    with app.state.sessions.begin() as db:
        parent = create_user(db, "parent.sync", "demo-synthetic-secret")
        child = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("Bé mẫu"))
        db.add(child)
        db.flush()
        cid = child.id
    with TestClient(app, base_url="https://testserver") as client:
        csrf = {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}
        login = client.post("/api/auth/login", json={"username": "parent.sync", "password": "demo-synthetic-secret"},
                            headers=csrf)
        csrf["X-CSRF-Token"] = login.json()["csrf_token"]
        yield app, client, cid, csrf


def pair_code(family):
    _, client, cid, csrf = family
    response = client.post(f"/api/children/{cid}/pairing", headers=csrf, json=CONSENT)
    assert response.status_code == 201, response.text
    return response.json()["code"]


def enroll(family):
    _, client, _, _ = family
    response = client.post("/api/enroll", json={"installation_id": str(uuid4()), "code": pair_code(family),
                                               "label": "Máy mẫu"})
    assert response.status_code == 201, response.text
    return response.json()


def payload(identity, **changes):
    return {"device_id": identity["device_id"], "child_id": identity["child_id"], "policy_id": 1,
            "ts": int(time.time()), "duration_sec": 0, "type": "domain_query",
            "subject": "private-label.school.edu.vn", **changes}


def test_explicit_consent_and_concurrent_single_use(family):
    _, client, cid, csrf = family
    assert client.post(f"/api/children/{cid}/pairing", json=CONSENT).status_code == 403
    for flag in CONSENT:
        assert client.post(f"/api/children/{cid}/pairing", json={**CONSENT, flag: False}, headers=csrf).status_code == 422
    code = pair_code(family)
    assert len(code) == 8
    def attempt(_):
        return client.post("/api/enroll", json={"installation_id": str(uuid4()), "code": code,
                                                "label": "Mẫu"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [201, 400]
    assert len(client.get(f"/api/children/{cid}/devices").json()) == 1


def test_expired_code_and_rate_limit(family):
    app, client, _, _ = family
    code = pair_code(family)
    with app.state.sessions.begin() as db:
        row = db.scalar(select(PairCode))
        assert row.digest != code and row.expires_at - row.created_at == 600
        row.expires_at = int(time.time())
    data = {"code": code, "installation_id": str(uuid4()), "label": "Mẫu"}
    for _ in range(10):
        assert client.post("/api/enroll", json=data).status_code == 400
    assert client.post("/api/enroll", json=data).status_code == 429


def test_device_refresh_binding_replay_expiry_and_revocation(family):
    app, client, cid, csrf = family
    identity = enroll(family)
    device_id = identity["device_id"]
    url = f"/api/devices/{device_id}"
    access = {"Authorization": "Bearer " + identity["access_token"]}
    refresh = {"refresh_token": identity["refresh_token"]}
    assert client.get(f"/api/devices/{uuid4()}/policy", headers=access).status_code == 401
    assert client.get(url + "/policy", headers=access).status_code == 200
    with app.state.sessions.begin() as db:
        row = db.get(Device, device_id)
        assert row.access_until - int(time.time()) in range(890, 901)
        assert row.refresh_hash != identity["refresh_token"] and "Mẫu" not in row.label
        row.access_until = int(time.time())
    assert client.get(url + "/policy", headers=access).status_code == 401
    fresh = client.post(url + "/refresh", json=refresh).json()
    assert fresh["access_token"] != identity["access_token"]
    assert client.post(url + "/refresh", json=refresh).status_code == 401
    assert client.delete(f"/api/children/{cid}/devices/{device_id}", headers=csrf).status_code == 204
    assert client.get(url + "/policy", headers={"Authorization": "Bearer " + fresh["access_token"]}).status_code == 401
    assert client.post(url + "/refresh", json={"refresh_token": fresh["refresh_token"]}).status_code == 401


def test_policy_cas_signature_replay_and_cache_expiry(family, tmp_path):
    _, client, cid, csrf = family
    identity = enroll(family)
    device_id, key = identity["device_id"], identity["verification_key"]
    access = {"Authorization": "Bearer " + identity["access_token"]}
    url = f"/api/devices/{device_id}/policy"
    initial = client.get(url, headers=access).json()
    cache = AgentStore(tmp_path / "cache.sqlite3", Fernet(Fernet.generate_key()))
    cache.save_identity(identity)
    now = int(time.time())
    assert cache.save_policy(initial, device_id, key, now)["version"] == 1
    changed = client.put(f"/api/children/{cid}/policy", json={"version": 1, "weekday_minutes": 3}, headers=csrf)
    assert changed.json()["weekday_minutes"] == 3 and changed.json()["version"] == 2
    assert client.put(f"/api/children/{cid}/policy", json={"version": 1}, headers=csrf).status_code == 409
    latest = client.get(url, headers=access).json()
    cache.save_policy(latest, device_id, key)
    assert cache.read_policy(device_id, key)["weekday_minutes"] == 3
    assert cache.read_policy(device_id, key, latest["policy"]["expires_at"]) is None
    with pytest.raises(ValueError): cache.save_policy(initial, device_id, key)
    with pytest.raises(ValueError): verified_policy(latest, str(uuid4()), key, now)
    latest["policy"]["weekday_minutes"] = 100
    with pytest.raises(ValueError): verified_policy(latest, device_id, key, now)


def test_heartbeat_policy_event_privacy_atomic_batch_and_retries(family):
    app, client, cid, csrf = family
    identity = enroll(family)
    prefix = f"/api/devices/{identity['device_id']}"
    access = {"Authorization": "Bearer " + identity["access_token"]}
    body = {"used_seconds": 25, "policy_version": 1, "status": "visible"}
    heartbeat = client.post(prefix + "/heartbeat", headers=access, json=body).json()
    assert heartbeat["policy_version"] == 1 and abs(heartbeat["server_time"] - time.time()) < 5
    first = {"event_id": str(uuid4()), "payload": payload(identity)}
    bad = {"event_id": str(uuid4()), "payload": payload(identity, child_id=cid + 1)}
    assert client.post(prefix + "/events", headers=access, json={"events": [first, bad]}).status_code == 403
    assert client.post(prefix + "/events", headers=access, json={"events": [first]}).json()["inserted"] == 1
    assert client.post(prefix + "/events", headers=access, json={"events": [first]}).json()["inserted"] == 0
    with app.state.sessions() as db:
        row = db.scalar(select(UsageEvent))
        assert app.state.vault.decrypt(row.subject) == "school.edu.vn" and "school" not in row.subject
        assert db.scalar(select(func.count(UsageEvent.id))) == 1


@pytest.mark.parametrize("subject", ["https://example.org/private", "example.org?search=q", "127.0.0.1",
                                     "private@example.org", "not-a-domain", "local.lan"])
def test_sensitive_or_unknown_domain_rejected(subject):
    with pytest.raises(ValueError): reporting_domain(subject)


def test_subject_minimization_and_strict_business_schema():
    assert reporting_domain("kid.lib.example.co.uk") == "example.co.uk"
    assert reporting_domain("go.project.github.io") == "project.github.io"
    identity = {"device_id": str(uuid4()), "child_id": 1}
    with pytest.raises(ValueError): UsagePayload.model_validate(payload(identity, url="https://example.org"))
    with pytest.raises(ValueError): UsagePayload.model_validate(payload(identity, subject=r"C:\Users\A\game.exe", type="app_start"))
    with pytest.raises(ValueError): UsagePayload.model_validate(payload(identity, subject="private note", type="locked"))
    with pytest.raises(ValueError): PolicySettings(schedule=["0"*47] * 7)


@pytest.mark.parametrize("url,pin", [("http://localhost.example", None), ("http://user@127.0.0.1", None),
                                     ("https://example.org", None), ("https://example.org/?secret", "a"*64)])
def test_transport_rejects_insecure_settings(url, pin):
    with pytest.raises(ValueError): Transport(url, pin, None)


class InProcessTransport:
    origin = "http://127.0.0.1:8000"

    def __init__(self, client):
        self.client = client
        self.lose_ack = False
        self.lose_refresh = False
        self.lose_wipe_ack = False
        self.calls = []

    def request(self, method, path, data=None, token=None):
        self.calls.append((method, path))
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self.client.request(method, path, json=data, headers=headers)
        if response.status_code >= 400:
            raise DeviceHTTPError(response.status_code)
        if self.lose_ack and path.endswith("/events"):
            self.lose_ack = False
            raise ConnectionError("Simulated loss after server commit")
        if self.lose_refresh and path.endswith("/refresh"):
            self.lose_refresh = False
            raise ConnectionError("Simulated lost refresh response")
        if self.lose_wipe_ack and path.endswith("/erasure") and data["acknowledge"]:
            self.lose_wipe_ack = False
            raise ConnectionError("Simulated lost wipe acknowledgement")
        return response.json() if response.content else {}


def test_full_agent_roundtrip_duplicate_recovery_and_offline_wipe(family, tmp_path):
    app, client, cid, csrf = family
    store = AgentStore(tmp_path / "agent.db", Fernet(Fernet.generate_key()))
    agent = AgentClient(store, InProcessTransport.origin)
    agent.transport = InProcessTransport(client)
    device = agent.enroll(pair_code(family), "Máy giả lập")
    assert agent.sync_once()["policy_version"] == 1
    identity = store.identity()
    assert store.read_policy(device, identity["verification_key"])["safe_search"]
    store.enqueue(payload(identity))
    assert store.batch()[0]["payload"]["subject"] == "school.edu.vn"
    agent.transport.lose_ack = True
    with pytest.raises(ConnectionError): agent.sync_once()
    assert len(store.batch()) == 1
    agent.sync_once()
    assert store.batch() == []
    with app.state.sessions() as db:
        assert db.scalar(select(func.count(UsageEvent.id))) == 1
    store.enqueue(payload(identity))
    response = client.request("DELETE", f"/api/children/{cid}", json={"confirm": True, "child_id": cid}, headers=csrf)
    assert response.json()["devices_pending"] == 1
    assert agent.sync_once() == {"erased": True}
    assert store.identity() is None and store.batch() == [] and not store.read_policy(device, identity["verification_key"])
    assert client.get("/api/erasures").json()[0]["acknowledged"] is True


@pytest.mark.parametrize("delete_before_retry", [True, False])
def test_erasure_after_lost_refresh_response(family, tmp_path, delete_before_retry):
    _, client, cid, csrf = family
    store = AgentStore(tmp_path / "agent.db", Fernet(Fernet.generate_key()))
    agent = AgentClient(store, InProcessTransport.origin)
    agent.transport = InProcessTransport(client)
    agent.enroll(pair_code(family), "Máy thử xoay token")
    identity = store.identity()
    identity["access_until"] = 0
    store.save_identity(identity)
    store.enqueue(payload(identity))
    agent.transport.lose_refresh = True
    with pytest.raises(ConnectionError):
        agent.sync_once()
    if not delete_before_retry:
        with pytest.raises(DeviceHTTPError):
            agent.sync_once()
        assert store.identity() is None
    result = client.request("DELETE", f"/api/children/{cid}", json={"confirm": True, "child_id": cid}, headers=csrf)
    assert result.json()["devices_pending"] == int(delete_before_retry)
    if delete_before_retry:
        assert agent.sync_once() == {"erased": True}
    assert store.identity() is None and store.batch() == []
    assert client.get("/api/erasures").json()[0]["acknowledged"] is True


def test_revoked_device_wipes_before_family_deletion(family, tmp_path):
    _, client, cid, csrf = family
    store = AgentStore(tmp_path / "agent.db", Fernet(Fernet.generate_key()))
    agent = AgentClient(store, InProcessTransport.origin)
    agent.transport = InProcessTransport(client)
    device = agent.enroll(pair_code(family), "Máy thu hồi")
    store.enqueue(payload(store.identity()))
    assert client.delete(f"/api/children/{cid}/devices/{device}", headers=csrf).status_code == 204
    with pytest.raises(DeviceHTTPError) as failure:
        agent.sync_once()
    assert failure.value.status == 401 and store.identity() is None and store.batch() == []
    erased = client.request("DELETE", f"/api/children/{cid}", json={"confirm": True, "child_id": cid}, headers=csrf)
    assert erased.json()["devices_pending"] == 0
    assert client.get("/api/erasures").json()[0]["acknowledged"] is True


def test_preexisting_receipt_and_device_migrate_without_reenrollment(tmp_path):
    url = f"sqlite:///{(tmp_path / 'legacy.sqlite3').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0003_family")
    engine = make_engine(url)
    try:
        with engine.begin() as db:
            db.execute(text("INSERT INTO users (id,username,password_hash,role,failures,locked_until) "
                            "VALUES(1,'synthetic','test-hash','parent',0,0)"))
            db.execute(text("INSERT INTO children (id,parent_id,name_cipher) VALUES(1,1,'test-cipher')"))
            db.execute(text("INSERT INTO devices (id,child_id,label,fingerprint_hash,access_hash,access_until,"
                            "refresh_hash,refresh_until,revoked,used_seconds,policy_version,consent_at,"
                            "consent_parent_id,consent_scope) VALUES('sample',1,'example','install','access',0,"
                            "'old-device-hash',0,0,0,1,1,1,'visible')"))
            db.execute(text("INSERT INTO erasures (device_id,parent_id,refresh_hash,created_at) "
                            "VALUES('detached',1,'old-receipt-hash',1)"))
        migrate(url)
        with engine.connect() as db:
            assert db.scalar(text("SELECT erasure_hash FROM devices WHERE id='sample'")) == "old-device-hash"
            assert db.scalar(text("SELECT proof_hash FROM erasures WHERE device_id='detached'")) == "old-receipt-hash"
    finally:
        engine.dispose()


@pytest.mark.parametrize("aged_out", [False, True])
def test_receipt_only_restart_and_fresh_pair_after_lost_ack(family, tmp_path, aged_out):
    app, browser, cid, csrf = family
    protection = Fernet(Fernet.generate_key())
    path = tmp_path / "agent.db"
    agent = AgentClient(AgentStore(path, protection), InProcessTransport.origin)
    http = agent.transport = InProcessTransport(browser)
    old_device = agent.enroll(pair_code(family), "Máy trước khi xóa")
    agent.store.enqueue(payload(agent.store.identity()))
    browser.request("DELETE", f"/api/children/{cid}", json={"confirm": True, "child_id": cid}, headers=csrf).raise_for_status()
    http.lose_wipe_ack = True
    with pytest.raises(ConnectionError):
        agent.sync_once()
    pending = agent.store.identity()
    assert set(pending) == {"device_id", "erasure_token", "origin", "erase_pending"}
    assert pending["erase_pending"] and not agent.store.batch()
    if aged_out:
        with app.state.sessions.begin() as db:
            db.execute(update(Erasure).values(acknowledged_at=int(time.time()) - 8*86400))
            prune_expired(db)
    restarted = AgentClient(AgentStore(path, protection), http.origin)
    restarted.transport = http
    http.calls.clear()
    result = restarted.retry_pending_receipt()
    assert http.calls == [("POST", f"/api/devices/{old_device}/erasure")]
    assert result["erased"] and restarted.store.identity() is None
    assert (result.get("receipt_status") == "unavailable") == aged_out
    assert ("không còn biên nhận" in erasure_message(result)) == aged_out
    new_child = browser.post("/api/children", json={"name": "Bé thử mới"}, headers=csrf).raise_for_status().json()
    code = browser.post(f"/api/children/{new_child['id']}/pairing", json=CONSENT, headers=csrf).raise_for_status().json()["code"]
    assert restarted.enroll(code, "Máy ghép mới") != old_device
    assert restarted.sync_once()["policy_version"] == 1


def test_tray_retries_receipt_before_enable_pairing(monkeypatch):
    """Drive the startup form without opening Windows UI or starting any collection."""
    from agent import tray
    tk = pytest.importorskip("tkinter")
    from tkinter import ttk
    client = Mock()
    client.transport.origin = "https://testserver"
    client.store.identity.return_value = {"erase_pending": True, "origin": client.transport.origin}
    client.retry_pending_receipt.side_effect = [ConnectionError(), {"erased": True, "receipt_status": "unavailable"}]
    clock, events, order, notices = 0, [], [], []
    closed = False
    window, entry, button = Mock(), Mock(), Mock()
    entry.get.return_value = "ABC23456"

    def after(delay, callback):
        order.append(callback)
        heapq.heappush(events, (clock + delay, len(order), callback))

    def mainloop():
        nonlocal clock
        while events and not closed:
            clock, _, callback = heapq.heappop(events)
            assert clock < 61000
            callback()

    def quit_window():
        nonlocal closed
        closed = True

    def button_factory(*args, command, **kwargs):
        def configure(*, state):
            if state == "normal":
                after(1, command)
        button.configure.side_effect = configure
        return button

    window.after.side_effect, window.mainloop.side_effect, window.quit.side_effect = after, mainloop, quit_window
    status = Mock()
    status.set.side_effect = notices.append
    monkeypatch.setattr(tk, "Tk", lambda: window)
    monkeypatch.setattr(tk, "StringVar", lambda **kw: status)
    monkeypatch.setattr(ttk, "Frame", Mock())
    monkeypatch.setattr(ttk, "Label", Mock())
    monkeypatch.setattr(ttk, "Entry", Mock(return_value=entry))
    monkeypatch.setattr(ttk, "Button", button_factory)
    monkeypatch.setattr(tray.threading, "Thread", lambda *, target, daemon: Mock(start=target))
    assert tray.prepare_enrollment(client)
    assert client.retry_pending_receipt.call_count == 2
    client.enroll.assert_called_once_with("ABC23456", "Máy mô phỏng")
    client.sync_once.assert_not_called()
    assert any("không còn biên nhận" in message for message in notices)


def test_bad_local_rows_are_isolated_before_upload(family, tmp_path, monkeypatch, caplog):
    app, browser, _, _ = family
    protector = Fernet(Fernet.generate_key())
    # Match WindowsProtection's contract without accessing the Windows key store.
    decrypt = protector.decrypt
    def unseal(raw):
        try:
            return decrypt(raw)
        except InvalidToken as error:
            raise ValueError("Unreadable fixture") from error
    monkeypatch.setattr(protector, "decrypt", unseal)
    path = tmp_path / "cache.db"
    agent = AgentClient(AgentStore(path, protector), InProcessTransport.origin)
    http = agent.transport = InProcessTransport(browser)
    agent.enroll(pair_code(family), "Máy thử bộ đệm")
    identity = agent.store.identity()
    valid_id = agent.store.enqueue(payload(identity))
    now = int(time.time())
    def sealed(event_key, business):
        return protector.encrypt(canonical({"event_id": event_key, "payload": business}))
    first, second, third, fourth = (str(uuid4()) for _ in range(4))
    invalid = [
        (first, now, b"broken ciphertext"),
        (second, now, sealed(second, payload(identity, ts=now, child_id=identity["child_id"] + 1,
                                             subject="other.synthetic.example.org"))),
        (third, now, sealed(third, payload(identity, ts=now, device_id=str(uuid4())))),
        (fourth, now, sealed(str(uuid4()), payload(identity, ts=now))),
    ]
    with agent.store.connection() as db:
        db.executemany("INSERT INTO outbox VALUES(?,?,?)", invalid)
    observe = Mock(wraps=http.request)
    http.request = observe
    agent.sync_once()
    posts = [call for call in observe.call_args_list if call.args[1].endswith("/events")]
    assert len(posts) == 1 and [e["event_id"] for e in posts[0].args[2]["events"]] == [valid_id]
    with app.state.sessions() as db:
        assert db.scalar(select(func.count(UsageEvent.id))) == 1
    assert "cách ly 4" in caplog.text and "other.synthetic" not in caplog.text
    restarted = AgentStore(path, protector)
    assert restarted.batch() == []
    with restarted.connection() as db:
        assert db.execute("SELECT count(*) FROM outbox_quarantine").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 4
    # Normal retention also removes quarantine markers and the original sealed blobs.
    restarted.batch(now=now + 91*86400)
    with restarted.connection() as db:
        assert db.execute("SELECT count(*) FROM outbox_quarantine").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_batch_refuses_stale_enrollment_and_defers_future_events(tmp_path):
    store = AgentStore(tmp_path / "cache.db", Fernet(Fernet.generate_key()))
    identity = {"child_id": 1, "device_id": str(uuid4()), "origin": "https://demo.example.org", "verification_key": "fixture"}
    store.save_identity(identity)
    now = int(time.time())
    valid = store.enqueue(payload(identity, ts=now))
    future = store.enqueue(payload(identity, ts=now + 3600), now=now + 3600)
    with pytest.raises(ValueError, match="ghép máy đã đổi"):
        store.batch(now=now, enrollment={**identity, "origin": "https://previous.example.org"})
    assert [e["event_id"] for e in store.batch(now=now, enrollment=identity)] == [valid]
    store.acknowledge([valid])
    assert [e["event_id"] for e in store.batch(now=now + 3600)] == [future]


@pytest.mark.parametrize("operation", ["events", "heartbeat", "ack", "refresh"])
def test_revocation_orders_before_waiting_token_write(family, operation):
    """A queued request must see revocation committed by the preceding SQLite writer."""
    app, browser, cid, csrf = family
    identity = enroll(family)
    did = identity["device_id"]
    cmd = browser.post(f"/api/children/{cid}/devices/{did}/commands", headers=csrf,
                       json={"action": "unlock"}).raise_for_status().json()["id"]
    path, data = {
        "events": ("events", {"events": [{"event_id": str(uuid4()), "payload": payload(identity)}]}),
        "heartbeat": ("heartbeat", {"used_seconds": 42, "policy_version": 1, "status": "visible"}),
        "ack": (f"commands/{cmd}/ack", None),
        "refresh": ("refresh", {"refresh_token": identity["refresh_token"]}),
    }[operation]
    waiting = threading.Event()
    def writer_requested(conn, cursor, statement, params, context, many):
        if statement == "BEGIN IMMEDIATE":
            waiting.set()

    with ThreadPoolExecutor(max_workers=1) as workers, app.state.sessions() as revoker:
        revoker.execute(text("BEGIN IMMEDIATE"))
        revoker.execute(update(Device).where(Device.id == did).values(revoked=True))
        sql_event.listen(app.state.engine, "before_cursor_execute", writer_requested)
        try:
            request = workers.submit(browser.post, f"/api/devices/{did}/{path}", json=data,
                                     headers={"Authorization": "Bearer " + identity["access_token"]})
            reached_writer = waiting.wait(timeout=5)
            revoker.commit()
            result = request.result(timeout=10)
        finally:
            sql_event.remove(app.state.engine, "before_cursor_execute", writer_requested)
    assert reached_writer and result.status_code == 401
    with app.state.sessions() as db:
        assert db.scalar(select(func.count(UsageEvent.id))) == 0
        device = db.get(Device, did)
        assert device.last_seen is None and device.used_seconds == 0 and device.policy_version == 0
        assert device.access_hash == digest(identity["access_token"])
        assert device.refresh_hash == digest(identity["refresh_token"])
        assert db.get(Command, cmd).acknowledged_at is None
