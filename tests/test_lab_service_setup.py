"""Lab service lifecycle with disposable files, synthetic parents, and no real SCM or ACL writes."""
import base64
from contextlib import nullcontext
from io import BytesIO
import json
import os
import time
from urllib.error import HTTPError
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent import lab_service
from scripts.lab import service_admin as admin
from server.accounts import create_user
from server.db import migrate
from server.lab_removal import Removal
from server.family_models import Erasure
from server.main import create_app
from server.models import AuditEntry, Child, LoginSession
from server.security import digest
from server.retention import ERASURE_ACK_HISTORY
from server.sync_models import Device

SID = "S-1-5-21-100000-200000-300000-1050"


class FakeSystem:
    def __init__(self, path):
        self.directory = path
        self.registered = False
        self.started = False
        self.recovered = False
        self.fail_recovery = False
        self.fail_remove = False

    def check_sid(self, sid):
        assert sid == SID

    def exists(self):
        return self.registered

    def make_directory(self):
        self.directory.mkdir()

    def write_new(self, name, content):
        with (self.directory / name).open("x", encoding="utf-8") as stream:
            stream.write(content)

    def check_directory(self):
        assert self.directory.is_dir()

    def create(self):
        assert not self.registered
        self.registered = True

    def recovery(self):
        if self.fail_recovery:
            raise RuntimeError("SCM fixture failure")
        self.recovered = True

    def remove(self):
        if self.fail_remove:
            raise TimeoutError("SCM fixture stopping")
        self.registered = self.started = False

    def cleanup(self):
        for name in ("installed", "removing", admin.LEGACY_OWNER, admin.CONFIG_FILE):
            (self.directory / name).unlink(missing_ok=True)
        self.directory.rmdir()


def config():
    return admin.enrollment(SID, 1, str(uuid4()), "https://localhost:8443", "a"*64, "")


def test_install_is_manual_and_uses_persistent_sid(tmp_path, monkeypatch):
    system, parent = FakeSystem(tmp_path / "service"), Mock(owner_id=17)
    supplied = config()
    admin.install(system, supplied, parent)
    assert system.registered and system.recovered and not system.started
    parent.check_device.assert_called_once_with()
    assert json.loads((system.directory / admin.CONFIG_FILE).read_text(encoding="utf-8")) == {**supplied, "owner_id": 17}
    checks = Mock()
    monkeypatch.setattr(lab_service, "check_data_acl", checks)
    assert lab_service.configured_sid(system.directory) == SID
    assert [c.kwargs["directory"] for c in checks.call_args_list] == [True, False]
    with pytest.raises(ValueError):
        admin.install(system, supplied, parent)
    assert system.registered


@pytest.mark.parametrize("failure", ["recovery", "parent", "existing_directory", "existing_service"])
def test_install_rollback_and_foreign_resources(tmp_path, failure):
    system, parent = FakeSystem(tmp_path / "service"), Mock(owner_id=17)
    if failure == "recovery":
        system.fail_recovery = True
    elif failure == "parent":
        parent.check_device.side_effect = ValueError("other family")
    elif failure == "existing_directory":
        system.directory.mkdir()
        (system.directory / "unrelated").write_text("untouched")
    else:
        system.registered = True
    with pytest.raises((RuntimeError, ValueError)):
        admin.install(system, config(), parent)
    assert system.registered == (failure == "existing_service")
    assert system.directory.exists() == (failure == "existing_directory")
    if failure == "existing_directory":
        assert (system.directory / "unrelated").read_text() == "untouched"


@pytest.mark.parametrize("failure", ["authorization", "stop", "report"])
def test_remove_retries_ordered_stages(tmp_path, failure):
    system, parent = FakeSystem(tmp_path / "service"), Mock(owner_id=17)
    admin.install(system, config(), parent)
    stages = []
    def step(stage, owner_id):
        assert owner_id == 17
        stages.append(stage)
        if stage == "report_removed":
            assert not system.registered
        if (stage == "authorize" and failure == "authorization") or (stage == "report_removed" and failure == "report"):
            raise ValueError("Synthetic refusal")
        return "recorded"
    parent.removal.side_effect = step
    system.fail_remove = failure == "stop"
    with pytest.raises((TimeoutError, ValueError)):
        admin.remove(system, parent)
    assert system.directory.is_dir() and system.registered == (failure != "report")
    assert stages == (["authorize", "report_removed"] if failure == "report" else ["authorize"])
    def retry(stage, owner_id):
        stages.append(stage)
        return "recorded"
    parent.removal.side_effect = retry
    system.fail_remove = False
    admin.remove(system, parent)
    assert stages[-2:] == ["authorize", "report_removed"] and not system.directory.exists()


def missing_target(payload=b'{"detail":{"code":"lab_removal_target_unavailable"}}', code=404):
    return HTTPError("https://example.invalid", code, "synthetic", {}, BytesIO(payload))


def terminal_parent(configuration, owner, responses):
    # Keep the production response parser and owner gate, replace only the network.
    parent = admin.ParentSession.__new__(admin.ParentSession)
    parent.config = configuration
    parent.owner_id = owner
    parent.request = Mock(side_effect=responses)
    return parent


ERASED_SCOPE = {"authorized": True, "reported_removed": False, "profile_erased": True}
AUTHORIZED = {"authorized": True, "reported_removed": False}


@pytest.mark.parametrize("at_stage", ["before_authorize", "before_report"])
@pytest.mark.parametrize("retained", [True, False])
def test_remove_erased_child_without_recreating_a_report(tmp_path, capsys, at_stage, retained):
    system = FakeSystem(tmp_path / "service")
    configuration = config()
    admin.install(system, configuration, Mock(owner_id=17))
    gone = ERASED_SCOPE if retained else missing_target()
    responses = [gone] if at_stage == "before_authorize" else [AUTHORIZED, gone]
    parent = terminal_parent(configuration, 17, responses)
    assert admin.remove(system, parent) == ("erased" if retained else "unavailable")
    assert not system.registered and not system.directory.exists()
    assert parent.request.call_count == len(responses)
    assert "gỡ service cục bộ" in capsys.readouterr().out


def test_wrong_parent_or_wrong_404_or_network_cannot_trigger_local_wipe(tmp_path):
    system = FakeSystem(tmp_path / "service")
    configuration = config()
    admin.install(system, configuration, Mock(owner_id=17))
    wrong = terminal_parent(configuration, 19, [missing_target()])
    with pytest.raises(ValueError, match="tài khoản phụ huynh"):
        admin.remove(system, wrong)
    wrong.request.assert_not_called()
    for error in (missing_target(b"<html>not found</html>"), missing_target(b'{"detail":"Not Found"}'),
                  missing_target(code=401), missing_target(code=403), TimeoutError("synthetic offline")):
        with pytest.raises((ValueError, OSError)):
            admin.remove(system, terminal_parent(configuration, 17, [error]))
        assert system.registered and not (system.directory / "removing").exists()


def test_legacy_owner_bound_before_scm_retry_after_erasure_expiry(tmp_path):
    system = FakeSystem(tmp_path / "service")
    configuration = config()
    admin.install(system, configuration, Mock(owner_id=17))
    # A previously shipped config and an interrupted legacy remove had no owner binding.
    (system.directory / admin.CONFIG_FILE).write_text(json.dumps(configuration), encoding="utf-8")
    system.write_new("removing", "")
    for user in (17, 19):
        with pytest.raises(ValueError, match="thiếu mã phụ huynh"):
            admin.remove(system, terminal_parent(configuration, user, [missing_target()]))
        assert system.registered and not (system.directory / admin.LEGACY_OWNER).exists()
    system.fail_remove = True
    with pytest.raises(TimeoutError):
        admin.remove(system, terminal_parent(configuration, 17, [ERASED_SCOPE]))
    marker = json.loads((system.directory / admin.LEGACY_OWNER).read_text(encoding="utf-8"))
    assert marker == {"owner_id": 17} and system.registered
    system.fail_remove = False
    with pytest.raises(ValueError, match="tài khoản phụ huynh"):
        admin.remove(system, terminal_parent(configuration, 19, [missing_target()]))
    assert admin.remove(system, terminal_parent(configuration, 17, [missing_target()])) == "unavailable"
    assert not system.registered and not system.directory.exists()


def test_network_loss_after_scm_then_erased_profile_retry(tmp_path):
    system = FakeSystem(tmp_path / "service")
    configuration = config()
    admin.install(system, configuration, Mock(owner_id=17))
    with pytest.raises(TimeoutError):
        admin.remove(system, terminal_parent(configuration, 17, [AUTHORIZED, TimeoutError("synthetic offline")]))
    assert not system.registered and (system.directory / "removing").exists()
    assert admin.remove(system, terminal_parent(configuration, 17, [ERASED_SCOPE])) == "erased"
    assert not system.directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="pywin32 security descriptor parsing")
@pytest.mark.parametrize("folder,sddl", [(True, admin.DATA_ACL), (False, admin.FILE_ACL)])
def test_acl_validates_in_memory_only(folder, sddl, monkeypatch):
    import win32security as sec
    path = Mock()
    path.lstat.return_value.st_file_attributes = 0
    def descriptor(s):
        return sec.ConvertStringSecurityDescriptorToSecurityDescriptor(s, 1)
    inspect = Mock(return_value=descriptor(sddl))
    monkeypatch.setattr(sec, "GetFileSecurity", inspect)
    lab_service.check_data_acl(path, directory=folder)
    for wrong in (sddl.replace("0x1200a9", "FA"), sddl.replace(";;;LS", ";;;BU"), sddl.replace("D:P", "D:")):
        inspect.return_value = descriptor(wrong)
        with pytest.raises(ValueError):
            lab_service.check_data_acl(path, directory=folder)
    path.lstat.return_value.st_file_attributes = 0x400
    with pytest.raises(ValueError, match="reparse"):
        lab_service.check_data_acl(path, directory=folder)


def test_admin_transport_requires_valid_pin_https_and_real_user_sid():
    for sid, origin, pin in [(SID, "http://127.0.0.1", None), (SID, "https://localhost", "bad"),
                             ("S-1-1-0", "https://localhost", "a"*64)]:
        with pytest.raises(ValueError):
            admin.enrollment(sid, 1, str(uuid4()), origin, pin, "")


@pytest.mark.skipif(os.name != "nt", reason="pywin32 numeric constants")
def test_scm_adapter_contract_with_stub_handles():
    import win32service as constants
    system = admin.WindowsService.__new__(admin.WindowsService)
    system.scm = Mock(spec=constants)
    for name in ("SC_MANAGER_CREATE_SERVICE", "SERVICE_QUERY_CONFIG", "SERVICE_CHANGE_CONFIG", "SERVICE_START",
                 "SERVICE_STOP", "SERVICE_QUERY_STATUS", "SERVICE_WIN32_OWN_PROCESS", "SERVICE_DEMAND_START",
                 "SERVICE_ERROR_NORMAL", "SERVICE_CONFIG_FAILURE_ACTIONS", "SERVICE_RUNNING", "SERVICE_STOPPED",
                 "SERVICE_STOP_PENDING", "SERVICE_NO_CHANGE", "SERVICE_DISABLED", "SERVICE_CONTROL_STOP"):
        setattr(system.scm, name, getattr(constants, name))
    system.image = '"synthetic-program.exe" --scm'
    system.manager = lambda _access: nullcontext("fake-manager")
    system.service = lambda _access: nullcontext("fake-service")
    # All Win32 call attributes are stubs; numeric constants alone come from pywin32.
    for name in ("CreateService", "CloseServiceHandle", "QueryServiceConfig", "ChangeServiceConfig2",
                 "QueryServiceConfig2", "StartService", "QueryServiceStatusEx", "ChangeServiceConfig",
                 "ControlService", "DeleteService"):
        setattr(system.scm, name, Mock())
    system.scm.CreateService.return_value = "fake-service"
    scm_config = (constants.SERVICE_WIN32_OWN_PROCESS, constants.SERVICE_DEMAND_START, 1,
                  system.image, "", 0, [], admin.SERVICE_ACCOUNT, "OpenGuard Kids Lab IPC")
    system.scm.QueryServiceConfig.return_value = scm_config
    system.scm.QueryServiceConfig2.return_value = {**admin.RECOVERY, "Actions": tuple(admin.RECOVERY["Actions"])}
    system.create()
    created = system.scm.CreateService.call_args.args
    assert created[4:6] == (constants.SERVICE_WIN32_OWN_PROCESS, constants.SERVICE_DEMAND_START)
    assert created[7] == system.image and created[-2:] == (admin.SERVICE_ACCOUNT, None)
    system.recovery()
    configured = system.scm.ChangeServiceConfig2.call_args.args[-1]
    assert configured == {"ResetPeriod": 86400, "RebootMsg": None, "Command": None,
                          "Actions": [(1, 60000), (1, 120000), (0, 0)]}
    system.scm.StartService.assert_not_called()
    system.scm.QueryServiceStatusEx.side_effect = [
        {"CurrentState": state} for state in (constants.SERVICE_STOPPED, constants.SERVICE_RUNNING,
                                              constants.SERVICE_RUNNING, constants.SERVICE_STOPPED)]
    system.start()
    system.scm.StartService.assert_called_once_with("fake-service", [])
    system.exists = Mock(side_effect=[True, False])
    system.remove()
    system.scm.ControlService.assert_called_once_with("fake-service", constants.SERVICE_CONTROL_STOP)
    system.scm.DeleteService.assert_called_once_with("fake-service")
    system.scm.QueryServiceConfig.return_value = (*scm_config[:3], "foreign.exe", *scm_config[4:])
    with pytest.raises(ValueError, match="khác"):
        system.check_image("fake-service")


def login(client, name):
    csrf = client.get("/api/auth/csrf").raise_for_status().json()["csrf_token"]
    result = client.post("/api/auth/login", headers={"X-CSRF-Token": csrf},
                         json={"username": name, "password": "synthetic-service-test"})
    return {"X-CSRF-Token": result.raise_for_status().json()["csrf_token"]}


def test_parent_recent_login_scope_revoke_and_distinct_audit_stages(tmp_path):
    url = f"sqlite:///{(tmp_path / 'family.db').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=base64.urlsafe_b64encode(os.urandom(32)).decode())
    with app.state.sessions.begin() as db:
        parent = create_user(db, "lab.owner", "synthetic-service-test")
        create_user(db, "lab.other", "synthetic-service-test")
        child = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("Trẻ mẫu"))
        db.add(child)
        db.flush()
        child_id = child.id
        create_user(db, "lab.reader", "synthetic-service-test", "child-view", child_id)
    with TestClient(app, base_url="https://testserver") as client:
        csrf = login(client, "lab.owner")
        child_path = f"/api/children/{child_id}"
        code = client.post(child_path + "/pairing", headers=csrf,
                           json={"consent": True, "child_informed": True, "child_agrees": True}).json()["code"]
        device = client.post("/api/enroll", json={"code": code, "installation_id": str(uuid4()),
                                                 "label": "Máy giả lập"}).raise_for_status().json()
        device_id = device["device_id"]
        path = f"{child_path}/devices/{device_id}/lab-removal"
        before, after = {"stage": "authorize"}, {"stage": "report_removed"}
        assert client.post(path, json=before).status_code == 403
        assert client.post(path, json=after, headers=csrf).status_code == 409
        assert client.post(path, json=before, headers=login(client, "lab.reader")).status_code == 403
        assert client.post(path, json=before, headers=login(client, "lab.other")).status_code == 404
        csrf = login(client, "lab.owner")
        with app.state.sessions.begin() as db:
            db.get(LoginSession, digest(client.cookies["ogk_session"])).expires_at -= 301
        assert client.post(path, json=before, headers=csrf).status_code == 401
        csrf = login(client, "lab.owner")
        for _ in range(2):
            assert client.post(path, json=before, headers=csrf).json() == {"authorized": True, "reported_removed": False}
        with app.state.sessions() as db:
            assert db.get(Device, device_id).revoked and db.get(Removal, device_id).reported_at is None
        assert client.get(f"/api/devices/{device_id}/policy",
                          headers={"Authorization": "Bearer "+device["access_token"]}).status_code == 401
        for _ in range(2):
            assert client.post(path, json=after, headers=csrf).json()["reported_removed"]
        with app.state.sessions() as db:
            stages = db.scalars(select(AuditEntry.action).where(AuditEntry.action.like("lab.%")).order_by(AuditEntry.id))
            assert list(stages) == ["lab.removal_authorized", "lab.removal_reported"]
        deleted = client.request("DELETE", child_path, json={"confirm": True, "child_id": child_id}, headers=csrf)
        assert deleted.raise_for_status().json()["devices_pending"] == 1
        with app.state.sessions() as db:
            assert db.get(Removal, device_id) is None
        for stage in (before, after):
            assert client.post(path, json=stage, headers=csrf).raise_for_status().json() == {
                "authorized": True, "reported_removed": False, "profile_erased": True}
        csrf_other = login(client, "lab.other")
        assert client.post(path, json=before, headers=csrf_other).status_code == 404
        csrf = login(client, "lab.owner")
        with app.state.sessions.begin() as db:
            assert db.get(Erasure, device_id).acknowledged_at is None
            assert db.scalar(select(AuditEntry.id).where(AuditEntry.action.like("lab.%"))) is None
            db.get(Erasure, device_id).acknowledged_at = int(time.time()) - ERASURE_ACK_HISTORY - 60
        for stage in (before, after):
            response = client.post(path, json=stage, headers=csrf)
            assert response.status_code == 404 and response.json()["detail"] == {"code":"lab_removal_target_unavailable"}
