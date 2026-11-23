"""Family paths with two browser sessions, one device and independent credentials."""
import base64
import os
import time
from uuid import uuid4
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from starlette.websockets import WebSocketDisconnect

from server.cli import create_user
from server.db import migrate
from server.family import ZONE, prune_expired
from server.family_models import Command, Erasure, FamilyRequest
from server.main import create_app
from server.models import AuditEntry, Child
from server.retention import COMMAND_HISTORY, ERASURE_ACK_HISTORY, FAMILY_HISTORY
from server.security import digest
from server.sync_models import UsageEvent


def login(client, name):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    data = client.post("/api/auth/login", json={"username":name, "password":"fake-family-test-password"},
                       headers={"X-CSRF-Token":csrf})
    assert data.status_code == 200
    return {"X-CSRF-Token":data.json()["csrf_token"]}


@pytest.fixture
def home(tmp_path):
    url = f"sqlite:///{(tmp_path/'family.sqlite3').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=base64.urlsafe_b64encode(os.urandom(32)).decode())
    with app.state.sessions.begin() as db:
        parent = create_user(db, "demo.testparent", "fake-family-test-password")
        profile = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("Mẫu"))
        db.add(profile)
        db.flush()
        child_id = profile.id
        create_user(db, "demo.testchild", "fake-family-test-password", "child-view", child_id)
    with TestClient(app,base_url="https://testserver") as browser, TestClient(app,base_url="https://testserver") as child:
        parent_csrf = login(browser, "demo.testparent")
        child_csrf = login(child, "demo.testchild")
        prefix = f"/api/children/{child_id}"
        code = browser.post(prefix+"/pairing",headers=parent_csrf,
                            json={"consent":True,"child_informed":True,"child_agrees":True}).json()["code"]
        identity = browser.post("/api/enroll",json={"installation_id":str(uuid4()),"code":code,"label":"Máy thử"}).json()
        yield app, browser, child, parent_csrf, child_csrf, prefix, identity


def test_parent_and_child_see_identical_event_report(home):
    _,browser,child,_,_,prefix,identity = home
    now = int(time.time())
    payload = {"ts":now, "policy_id":1, "device_id":identity["device_id"],"child_id":identity["child_id"]}
    events = [{"event_id":str(uuid4()),"payload":{**payload,"type":kind,"subject":subject,"duration_sec":sec}}
              for kind,subject,sec in [("app_stop","lesson.exe",90),("blocked_domain","page.example.org",0)]]
    result=browser.post(f"/api/devices/{identity['device_id']}/events", json={"events":events},
                        headers={"Authorization":"Bearer "+identity["access_token"]})
    assert result.status_code==200
    summary=browser.get(prefix+"/reports").json()
    assert summary==child.get(prefix+"/reports").json()
    assert summary["apps"]==[["lesson.exe",90]] and summary["domains"]==[["example.org",1]]
    assert summary["today_seconds"]==90 and summary["blocked"]==1


def test_report_splits_a_session_at_local_midnight(home):
    app, browser, _, _, _, prefix, ident = home
    yesterday = datetime.now(ZONE).date() - timedelta(days=1)
    end = datetime.combine(yesterday, datetime.min.time(), ZONE) + timedelta(minutes=2)
    with app.state.sessions.begin() as db:
        db.add(UsageEvent(event_key=str(uuid4()), device_id=ident["device_id"], child_id=ident["child_id"],
                          ts=int(end.timestamp()), type="app_stop", subject=app.state.vault.encrypt("sample.exe"),
                          duration_sec=180, policy_id=1))
    days = {row["date"]:row["seconds"] for row in browser.get(prefix+"/reports").json()["days"]}
    assert days[yesterday.isoformat()] == 120
    assert days[(yesterday - timedelta(days=1)).isoformat()] == 60


def test_child_feedback_and_single_device_grant(home):
    app,browser,child,pc,cc,prefix,ident=home
    ask=child.post(prefix+"/requests",headers=cc,json={"kind":"time"})
    assert ask.status_code==201
    url=f"{prefix}/requests/{ask.json()['id']}"
    decision={"status":"approved","answer":"Bố đồng ý, em nhớ lưu bài nhé.","device_id":ident["device_id"]}
    assert child.post(url,headers=cc,json=decision).status_code==403
    assert browser.post(url,headers=pc,json=decision).status_code==200
    assert browser.post(url,headers=pc,json=decision).status_code==409
    assert child.get(prefix+"/requests").json()[0]["answer"]==decision["answer"]
    with app.state.sessions() as db:
        assert len(db.scalars(select(Command)).all())==1


def test_child_domain_request_only_keeps_registrable_domain(home):
    _,browser,child,pc,cc,prefix,_=home
    assert child.post(prefix+"/requests",headers=cc,json={"kind":"domain","domain":"example.org/path"}).status_code==422
    row=child.post(prefix+"/requests",headers=cc,json={"kind":"domain","domain":"class.school.edu.vn"}).json()
    assert browser.get(prefix+"/requests").json()[0]["domain"]=="school.edu.vn"
    browser.post(f"{prefix}/requests/{row['id']}",headers=pc,json={"status":"declined","answer":"Mình cùng xem lại sau nhé."}).raise_for_status()
    assert child.get(prefix+"/requests").json()[0]["status"]=="declined"


def test_websocket_to_ack_and_retry(home):
    _,browser,_,pc,_,prefix,identity=home
    d=identity["device_id"]; headers={"Authorization":"Bearer "+identity["access_token"]}
    path=f"/api/devices/{d}/commands"
    issued=browser.post(prefix+f"/devices/{d}/commands",headers=pc,json={"action":"unlock"}).json()
    assert browser.get(prefix+"/commands").json()[0]["status"]=="queued"
    with browser.websocket_connect("wss://testserver"+path+"/ws",headers=headers) as ws:
        command=ws.receive_json()["commands"][0]
        assert command["id"]==issued["id"] and command["action"]=="unlock"
    for _ in range(2):
        assert browser.post(path+f"/{command['id']}/ack",headers=headers).status_code==200
    assert browser.get(prefix+"/commands").json()[0]["status"]=="acknowledged"
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect("wss://testserver"+path+"/ws"): pass


def test_policy_notification_after_write_and_on_reconnect(home):
    _, parent, _, csrf, _, family, device = home
    headers = {"Authorization": "Bearer " + device["access_token"]}
    ws_url = f"wss://testserver/api/devices/{device['device_id']}/commands/ws?policy_version=1"
    with parent.websocket_connect(ws_url, headers=headers) as channel:
        parent.put(family + "/policy", headers=csrf, json={"version": 1, "weekday_minutes": 7}).raise_for_status()
        assert channel.receive_json() == {"commands": [], "policy_version": 2}
    issued = parent.post(f"{family}/devices/{device['device_id']}/commands", headers=csrf,
                         json={"action": "add_15"}).raise_for_status().json()
    with parent.websocket_connect(ws_url, headers=headers) as channel:
        frame = channel.receive_json()
        assert frame["policy_version"] == 2 and frame["commands"][0]["id"] == issued["id"]


def test_erasure_closes_sessions_and_tracks_offline_ack(home):
    _,browser,child,pc,cc,prefix,identity=home
    d=identity["device_id"]; proof={"erasure_token":identity["erasure_token"]}
    assert child.request("DELETE",prefix,headers=cc,json={"confirm":True,"child_id":identity["child_id"]}).status_code==403
    result=browser.request("DELETE",prefix,headers=pc,json={"confirm":True,"child_id":identity["child_id"]})
    assert result.json()=={"server_erased":True,"devices_pending":1}
    assert browser.get(prefix+"/reports").status_code==404 and child.get("/api/auth/me").status_code==401
    assert not browser.get("/api/erasures").json()[0]["acknowledged"]
    assert browser.post(f"/api/devices/{d}/erasure",json={"refresh_token":"incorrect-proof-here"}).status_code==404
    assert browser.post(f"/api/devices/{d}/erasure",json={"erasure_token":identity["refresh_token"]}).status_code==404
    assert browser.post(f"/api/devices/{d}/erasure",json={**proof,"acknowledge":True}).json()=={"erase":True}
    assert browser.get("/api/erasures").json()[0]["acknowledged"]


def test_stale_browser_cannot_delete_replacement_child(home):
    _, parent, _, csrf, _, prefix, identity = home
    confirmation = {"confirm":True, "child_id":identity["child_id"]}
    parent.request("DELETE", prefix, headers=csrf, json=confirmation).raise_for_status()
    new = parent.post("/api/children", headers=csrf, json={"name":"Hồ sơ mới"}).json()
    assert new["id"] > identity["child_id"]
    assert parent.request("DELETE", prefix, headers=csrf, json=confirmation).status_code == 404
    assert parent.get(f"/api/children/{new['id']}/policy").status_code == 200


def test_retention_removes_only_old_activity(home):
    app,_,_,_,_,_,identity=home
    with app.state.sessions.begin() as db:
        for age in [5,91]:
            db.add(UsageEvent(event_key=str(uuid4()),device_id=identity["device_id"],child_id=identity["child_id"],
                ts=int(time.time())-age*86400,type="app_stop",subject=app.state.vault.encrypt("demo.exe"),duration_sec=2,policy_id=1))
        db.flush()
        prune_expired(db)
        assert len(db.scalars(select(UsageEvent)).all())==1


def test_remaining_reading_clears_when_tray_pauses(home):
    _, browser, child, _, _, prefix, identity = home
    url = f"/api/devices/{identity['device_id']}/heartbeat"
    payload = {"used_seconds":120, "remaining_seconds":960, "policy_version":1, "status":"visible"}
    access = {"Authorization":"Bearer "+identity["access_token"]}
    browser.post(url, headers=access, json=payload).raise_for_status()
    assert child.get(prefix+"/devices").json()[0]["remaining_seconds"] == 960
    browser.post(url, headers=access, json={**payload, "status":"paused"}).raise_for_status()
    reading = child.get(prefix+"/devices").json()[0]
    assert reading["status"] == "paused" and reading["remaining_seconds"] is None


def test_expired_family_history_inaccessible_before_hourly_cleanup(home, monkeypatch):
    app, parent, child, parent_csrf, child_csrf, prefix, identity = home
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    did, cid = identity["device_id"], identity["child_id"]
    ask = child.post(prefix + "/requests", headers=child_csrf, json={"kind":"time"}).json()["id"]
    old_command = parent.post(f"{prefix}/devices/{did}/commands", headers=parent_csrf, json={"action":"unlock"}).json()["id"]
    proof, old_receipt, pending_receipt = "synthetic-erasure-proof", str(uuid4()), str(uuid4())
    with app.state.sessions.begin() as db:
        profile = db.get(Child, cid)
        db.get(FamilyRequest, ask).created_at = now - FAMILY_HISTORY - 1
        db.get(Command, old_command).expires_at = now - COMMAND_HISTORY - 1
        db.execute(update(AuditEntry).where(AuditEntry.child_id == cid).values(ts=now - FAMILY_HISTORY - 1))
        for receipt_id, ack in [(old_receipt, now - ERASURE_ACK_HISTORY - 1), (pending_receipt, None)]:
            db.add(Erasure(device_id=receipt_id, parent_id=profile.parent_id, proof_hash=digest(proof),
                           created_at=now-FAMILY_HISTORY-1, acknowledged_at=ack))
    for viewer in (parent, child):
        for route in ("requests", "audit", "commands"):
            assert viewer.get(f"{prefix}/{route}").json() == []
    assert parent.post(f"{prefix}/requests/{ask}", headers=parent_csrf,
        json={"status":"approved", "answer":"Phản hồi thử", "device_id":did}).status_code == 404
    assert [r["device_id"] for r in parent.get("/api/erasures").json()] == [pending_receipt]
    assert parent.post(f"/api/devices/{old_receipt}/erasure", json={"erasure_token":proof}).status_code == 404
    assert parent.post(f"/api/devices/{pending_receipt}/erasure", json={"erasure_token":proof}).json() == {"erase":True}
    with app.state.sessions() as db:
        assert db.get(FamilyRequest, ask) is not None  # Deadline enforcement precedes physical cleanup.
    with TestClient(app, base_url="https://testserver"):
        with app.state.sessions() as db:
            assert db.get(FamilyRequest, ask) is None and db.get(Command, old_command) is None
            assert not db.scalars(select(AuditEntry).where(AuditEntry.child_id == cid)).all()
            assert db.get(Erasure, old_receipt) is None and db.get(Erasure, pending_receipt) is not None
