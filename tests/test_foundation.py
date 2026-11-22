import base64
import os
from uuid import UUID, uuid4

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from server import auth
from server.cli import create_user
from server.db import migrate
from server.main import create_app
from server.models import AuditEntry, Child, LoginSession, LoginWindow, User
from server.security import Vault, digest, hash_password, normalize_username, verify_password


@pytest.fixture
def key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


@pytest.fixture
def system(tmp_path, key):
    url = f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=key)
    with app.state.sessions.begin() as db:
        parent = create_user(db, "parent.one", "a-safe-demo-password")
        child = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("Bé Mẫu"))
        db.add(child)
        db.flush()
        create_user(db, "reader.one", "a-safe-demo-password", "child-view", child.id)
        other = create_user(db, "parent.two", "a-safe-demo-password")
        other_child = Child(parent_id=other.id, name_cipher=app.state.vault.encrypt("Bạn Mẫu"))
        db.add(other_child)
        db.flush()
        ids = (child.id, other_child.id)
    with TestClient(app, base_url="https://testserver") as client:
        yield app, client, ids


def login(client, username="parent.one", password="a-safe-demo-password"):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.post("/api/auth/login", headers={"X-CSRF-Token": token}, json={"username": username, "password": password})


def test_secret_key_format(key):
    assert len(Vault(key).derive("A")) == 32
    for invalid in ("", " ", "abc", "YQ==", key + "!"):
        with pytest.raises(ValueError):
            Vault(invalid)


def test_encryption_randomized_authenticated(key):
    vault = Vault(key)
    a, b = vault.encrypt("Bé mẫu"), vault.encrypt("Bé mẫu")
    assert a != b and vault.decrypt(a) == "Bé mẫu"
    with pytest.raises(InvalidTag):
        Vault(base64.urlsafe_b64encode(os.urandom(32)).decode()).decrypt(a)


def test_keys_separated(key):
    vault = Vault(key)
    assert vault.tag("ip", "same") != vault.tag("csrf", "same")
    assert vault.tag("csrf", "one") != vault.tag("csrf", "two")


@pytest.mark.parametrize("value", ["ab", "a@b", "Ab c", "A"*65])
def test_invalid_login_identifier(value):
    with pytest.raises(ValueError):
        normalize_username(value)


def test_username_normalization():
    assert normalize_username(" Parent.ONE ") == "parent.one"


def test_password_hash():
    password = "this-is-only-a-test"
    first, second = hash_password(password), hash_password(password)
    assert password not in first and first != second
    assert verify_password(password, first)
    assert not verify_password("different-demo-value", first)
    assert not verify_password(password, "broken")


@pytest.mark.parametrize("password", ["", "123456789", "x" * 257])
def test_password_length(password):
    with pytest.raises(ValueError):
        hash_password(password)


def test_health_migration_repeat_and_sqlite_pragmas(system):
    app, client, _ = system
    migrate(str(app.state.engine.url))
    assert client.get("/health").json() == {"status": "ok"}
    with app.state.engine.connect() as conn:
        assert conn.scalar(text("PRAGMA journal_mode")) == "wal"
        assert conn.scalar(text("PRAGMA foreign_keys")) == 1
        assert conn.scalar(text("PRAGMA secure_delete")) == 1


def test_tls_csrf_session_and_logout(system):
    app, client, _ = system
    assert client.get("http://testserver/health").status_code == 400
    assert client.get("/api/children").status_code == 401
    assert client.post("/api/auth/login", json={"username": "parent.one", "password": "sample"}).status_code == 403
    old_csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    response = login(client)
    assert response.status_code == 200
    token = client.cookies.get("ogk_session")
    cookie = response.headers.get_list("set-cookie")[0]
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=43200"))
    with app.state.sessions() as db:
        assert db.get(LoginSession, token) is None
        assert db.get(LoginSession, digest(token)) is not None
    csrf = response.json()["csrf_token"]
    assert csrf != old_csrf
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": old_csrf}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "https://elsewhere.example"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/auth/me", headers={"Cookie": f"ogk_session={token}"}).status_code == 401


def test_session_expiration_at_twelve_hours(system, monkeypatch):
    _, client, _ = system
    assert login(client).status_code == 200
    timestamp = auth.now()
    monkeypatch.setattr(auth, "now", lambda: timestamp + auth.SESSION_LIFETIME)
    assert client.get("/api/auth/me").status_code == 401


def test_lockout_at_five_and_unlock_after_fifteen_minutes(system, monkeypatch):
    _, client, _ = system
    timestamp = auth.now()
    monkeypatch.setattr(auth, "now", lambda: timestamp)
    absent = login(client, username="not.registered", password="bad").json()
    for _ in range(5):
        result = login(client, password="bad")
        assert result.status_code == 401 and result.json() == absent
    assert login(client).status_code == 401
    timestamp += auth.LOCKOUT
    assert login(client).status_code == 200


def test_ip_throttle_without_storing_plain_ip(system, monkeypatch):
    app, client, _ = system
    monkeypatch.setattr(auth, "IP_LIMIT", 2)
    assert login(client, "unknown.one").status_code == 401
    assert login(client, "unknown.two").status_code == 401
    assert login(client, "unknown.three").status_code == 429
    with app.state.sessions() as db:
        rows = db.scalars(select(LoginWindow)).all()
        assert len(rows) == 1 and len(rows[0].bucket) == 64 and rows[0].attempts == 2


def test_child_scope_and_audit_visibility(system):
    app, client, (child, other) = system
    csrf = {"X-CSRF-Token": login(client).json()["csrf_token"]}
    new = client.post("/api/children", json={"name": "Bé Gấu"}, headers=csrf)
    assert new.status_code == 201
    own = new.json()["id"]
    items = client.get("/api/children").json()
    assert {item["id"] for item in items} == {own, child}
    assert client.get(f"/api/children/{other}/audit").status_code == 404
    row = client.get(f"/api/children/{own}/audit").json()[0]
    assert row["action"] == "child.created" and row["after"]["name"] == "Bé Gấu" and row["ip"]
    with app.state.sessions.begin() as db:
        assert "Gấu" not in db.get(Child, own).name_cipher
        assert "Gấu" not in db.scalar(select(AuditEntry.after_cipher).where(AuditEntry.child_id == own))
        create_user(db, "reader.new", "a-safe-demo-password", "child-view", own)
    child_headers = {"X-CSRF-Token": login(client, "reader.new").json()["csrf_token"]}
    assert client.get("/api/children").json() == [new.json()]
    assert "ip" not in client.get(f"/api/children/{own}/audit").json()[0]
    assert client.get(f"/api/children/{child}/audit").status_code == 404
    assert client.post("/api/children", json={"name": "Other"}, headers=child_headers).status_code == 403


def test_admin_role_is_separate(system):
    app, client, (child, _) = system
    with app.state.sessions.begin() as db:
        create_user(db, "operator", "a-safe-demo-password", "admin")
    assert login(client).status_code == 200
    assert client.get("/api/admin/status").status_code == 403
    assert login(client, "operator").status_code == 200
    assert set(client.get("/api/admin/status").json()) == {"accounts", "children"}
    assert client.get(f"/api/children/{child}/audit").status_code == 403


def test_deleting_child_revokes_linked_reader_session(system):
    app, client, (child, _) = system
    assert login(client, "reader.one").status_code == 200
    with app.state.sessions.begin() as db:
        db.execute(delete(Child).where(Child.id == child))
    assert client.get("/api/auth/me").status_code == 401


def test_sensitive_inputs_absent_from_validation_error(system):
    _, client, _ = system
    marker = "private-test-value" * 20
    headers = {"X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"]}
    bad = client.post("/api/auth/login", json={"username": "parent.one", "password": marker}, headers=headers)
    assert bad.status_code == 422 and marker not in bad.text


@pytest.mark.parametrize("logger_name,source", [
    ("uvicorn.error", "lib/websockets/legacy/server.py"),
    ("uvicorn.error", "lib/uvicorn/protocols/http/h11_impl.py"),
    ("websockets.server", "lib/websockets/asyncio/server.py"),
    ("uvicorn.asgi", "lib/uvicorn/middleware/message_logger.py"),
])
def test_transport_error_keeps_location_and_removes_private_fields(logger_name, source):
    import logging
    import sys

    from server.main import _private_transport_logs

    marker = "synthetic-private-request"
    try:
        raise ValueError(marker)
    except ValueError:
        record = logging.getLogger(logger_name).makeRecord(
            logger_name, logging.ERROR, source, 208, marker + " %s", (marker,), sys.exc_info(),
            func="handle", extra={"color_message":marker, "websocket": {"headers":marker}}, sinfo=marker)
    record.exc_text = record.message = marker  # An earlier formatter may have cached these.
    assert _private_transport_logs(record)
    assert record.levelno == logging.ERROR and "208" in record.getMessage() and "handle" in record.getMessage()
    assert record.filename in record.getMessage()
    assert marker not in logging.Formatter().format(record) and marker not in repr(vars(record))


def test_uvicorn_transport_does_not_log_private_request_targets(tmp_path, key):
    import io
    import logging
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    url = f"sqlite:///{(tmp_path / 'privacy.sqlite3').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=key, secure_cookies=False)
    server = uvicorn.Server(uvicorn.Config(app, access_log=True, log_config=None, proxy_headers=False))
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    loggers = [logging.getLogger(name) for name in ("uvicorn.error", "uvicorn.access")]
    levels = [logger.level for logger in loggers]
    marker = "private-demo-query"
    device = str(uuid4())
    try:
        for logger in loggers:
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            runner = threading.Thread(target=server.run, kwargs={"sockets":[listener]}, daemon=True)
            runner.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and runner.is_alive() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert server.started
                with httpx.Client(trust_env=False, timeout=3) as http:
                    assert http.get(f"http://127.0.0.1:{port}/health?demo={marker}").status_code == 200
                with pytest.raises(InvalidStatus):
                    with connect(f"ws://127.0.0.1:{port}/api/devices/{device}/commands/ws?demo={marker}",
                                 proxy=None, open_timeout=3):
                        pytest.fail("Missing device token must not open a command channel.")
                loggers[0].warning("Synthetic diagnostic retained")
            finally:
                server.should_exit = True
                runner.join(timeout=6)
        captured = buf.getvalue()
        assert "Synthetic diagnostic retained" in captured
        assert marker not in captured and device not in captured and "127.0.0.1:" not in captured
    finally:
        for logger, level in zip(loggers, levels):
            logger.removeHandler(handler)
            logger.setLevel(level)


def test_parent_provisions_one_child_reader(system):
    _, client, (_, other) = system
    headers = {"X-CSRF-Token": login(client).json()["csrf_token"]}
    child = client.post("/api/children", json={"name": "Gấu"}, headers=headers).json()["id"]
    body = {"username": "reader.bear", "password": "test-only-password"}
    assert client.post(f"/api/children/{other}/account", headers=headers, json=body).status_code == 404
    endpoint = f"/api/children/{child}/account"
    assert client.post(endpoint, json=body).status_code == 403
    assert client.post(endpoint, headers=headers, json=body).status_code == 201
    assert client.post(endpoint, headers=headers, json=dict(body, username="reader.second")).status_code == 409
    assert body["password"] not in client.get(f"/api/children/{child}/audit").text
    assert login(client, body["username"], body["password"]).json()["child_id"] == child


def test_password_change_revokes_all_sessions(system):
    app, client, _ = system
    csrf = {"X-CSRF-Token": login(client).json()["csrf_token"]}
    new_password = "replacement-test-password"
    prev = client.cookies.get("ogk_session")
    with TestClient(app, base_url="https://testserver") as second:
        assert login(second).status_code == 200
        assert client.post("/api/auth/password", json={"current_password": "wrong", "new_password": new_password},
                           headers=csrf).status_code == 403
        assert client.post("/api/auth/password", json={"current_password": "a-safe-demo-password", "new_password": new_password},
                           headers=csrf).status_code == 204
        assert second.get("/api/auth/me").status_code == 401
        assert client.get("/api/auth/me", headers={"Cookie": f"ogk_session={prev}"}).status_code == 401
        assert login(client).status_code == 401
        assert login(client, password=new_password).status_code == 200


def test_opt_in_demo_history_requires_enrolled_demo_and_retries(system):
    from server.demo_history import seed_history
    from server.sync_models import Device, UsageEvent

    app, client, _ = system
    vault = app.state.vault
    with app.state.sessions.begin() as db:
        parent = create_user(db, "demo.parent", "test-demo-password")
        child = Child(parent_id=parent.id, name_cipher=vault.encrypt("An (hồ sơ mẫu)"))
        db.add(child)
        db.flush()
        cid = child.id
        with pytest.raises(ValueError):
            seed_history(db, vault, uuid4())
    headers = {"X-CSRF-Token": login(client, "demo.parent", "test-demo-password").json()["csrf_token"]}
    code = client.post(f"/api/children/{cid}/pairing", headers=headers,
                       json={"consent": True, "child_agrees": True, "child_informed": True}).json()["code"]
    device = client.post("/api/enroll", json={"code": code, "installation_id": str(uuid4()), "label": "Demo"}).json()
    with app.state.sessions.begin() as db:
        did = UUID(device["device_id"])
        count = seed_history(db, vault, did)
        assert 30 < count <= 40 and seed_history(db, vault, did) == 0
        assert db.scalar(select(func.count()).select_from(UsageEvent)) == count
        assert db.scalar(select(func.count()).select_from(Device)) == 1
        db.get(Device, str(did)).label = vault.encrypt("Máy thường")
        with pytest.raises(ValueError):
            seed_history(db, vault, did)
    report = client.get(f"/api/children/{cid}/reports").json()
    assert report["today_seconds"] > 0 and report["apps"][0][0].startswith("demo-")
