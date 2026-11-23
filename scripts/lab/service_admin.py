"""Explicit lab-only SCM lifecycle. The PowerShell launcher first verifies the installed runtime."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import getpass
from http.cookiejar import CookieJar
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID

# Isolated Python omits the script directory. Add only the install root verified by the launcher.
ROOT = Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from agent.client import Transport
from agent.lab_service import CHILD_SID, CONFIG_FILE, SERVICE, check_data_acl, data_root


DATA_ACL = "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;LS)"
FILE_ACL = "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;LS)"
LEGACY_OWNER = "removal-owner.json"
MARKERS = {"installed", "removing", LEGACY_OWNER}
SERVICE_ACCOUNT = r"NT AUTHORITY\LocalService"
RECOVERY = {"ResetPeriod": 86400, "RebootMsg": None, "Command": None, "Actions": [(1, 60000), (1, 120000), (0, 0)]}


def enrollment(child_sid, child_id, device_id, origin, pin, ca_pem):
    if not CHILD_SID.fullmatch(child_sid) or child_id < 1:
        raise ValueError("Cần SID Standard User và mã hồ sơ hợp lệ.")
    transport = Transport(origin, pin, None)
    if transport.context is None:
        raise ValueError("Thao tác quản trị lab yêu cầu HTTPS và mã ghim chứng chỉ.")
    if ca_pem:
        transport.context.load_verify_locations(cadata=ca_pem)
    return {"schema": 1, "child_sid": child_sid, "child_id": child_id, "device_id": str(UUID(device_id)),
            "origin": transport.origin, "pin": pin, "ca_pem": ca_pem}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ParentSession:
    """Short-lived terminal-only browser session. No password/cookie is passed to SCM or stored."""
    def __init__(self, config):
        self.config = config
        context = Transport(config["origin"], config["pin"], None).context
        if context is None:
            raise ValueError("Chỉ dùng HTTPS cho đăng nhập phụ huynh.")
        if config["ca_pem"]:
            context.load_verify_locations(cadata=config["ca_pem"])
        self.http = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context),
                                 HTTPCookieProcessor(CookieJar()))
        self.csrf = ""
        self.owner_id = None

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(self.config["origin"] + path, method=method, data=body,
                      headers={"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        with self.http.open(req, timeout=8) as response:
            data = response.read(1024*1024 + 1)
            if len(data) > 1024*1024:
                raise ValueError("Phản hồi quản trị vượt giới hạn.")
            return json.loads(data) if data else {}

    def __enter__(self):
        self.csrf = self.request("GET", "/api/auth/csrf")["csrf_token"]
        login = self.request("POST", "/api/auth/login",
                             {"username": input("Tài khoản phụ huynh: "), "password": getpass.getpass("Mật khẩu: ")})
        self.csrf = login["csrf_token"]
        if login["role"] != "parent" or type(login.get("id")) is not int or login["id"] <= 0:
            self.__exit__(None, None, None)
            raise ValueError("Cần tài khoản phụ huynh của thiết bị.")
        self.owner_id = login["id"]
        return self

    def __exit__(self, *_args):
        try:
            self.request("POST", "/api/auth/logout")
        except (OSError, ValueError):
            print("Chưa xác nhận đăng xuất trên máy chủ. Phiên terminal đã kết thúc.", file=sys.stderr)
        finally:
            self.http.close()
            self.csrf = ""
            self.owner_id = None

    def check_device(self):
        devices = self.request("GET", f"/api/children/{self.config['child_id']}/devices")
        if not any(row["id"] == self.config["device_id"] and not row["revoked"] for row in devices):
            raise ValueError("Không có thiết bị còn ghép phù hợp trong hồ sơ của phụ huynh.")

    def removal(self, stage, saved_owner):
        if saved_owner is not None and saved_owner != self.owner_id:
            raise ValueError("Cần đăng nhập bằng tài khoản phụ huynh đã xác nhận bản cài này.")
        path = f"/api/children/{self.config['child_id']}/devices/{self.config['device_id']}/lab-removal"
        try:
            result = self.request("POST", path, {"stage": stage})
        except HTTPError as error:
            if error.code != 404:
                raise
            try:
                # Only the application's authenticated response is meaningful, never an arbitrary page or proxy.
                detail = json.loads(error.read(1024)).get("detail")
            except (UnicodeError, ValueError, AttributeError):
                raise ValueError("Không xác minh được phản hồi 404 của API gỡ service.") from error
            finally:
                error.close()
            if detail != {"code": "lab_removal_target_unavailable"}:
                raise ValueError("Không xác minh được phản hồi 404 của API gỡ service.") from error
            if saved_owner is None:
                raise ValueError("Bản cài cũ thiếu mã phụ huynh và máy chủ không còn biên nhận khớp.") from error
            return "unavailable"
        if result == {"authorized": True, "reported_removed": False, "profile_erased": True}:
            return "erased"
        if result.get("authorized") is not True or (stage == "report_removed" and result.get("reported_removed") is not True):
            raise ValueError("Máy chủ chưa xác nhận bước gỡ service.")
        return "recorded"


def security_attributes(sddl):
    import pywintypes
    import win32security
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    return attrs


class WindowsService:
    """Fixed directory, identity and binary. Refuse to adopt unrelated files or services."""
    def __init__(self):
        import win32service as scm
        self.scm = scm
        self.directory = data_root()
        self.image = f'"{sys.executable}" -I -B "{ROOT / "agent" / "lab_service.py"}" --scm'

    def check_sid(self, sid):
        import win32api
        import win32net
        import win32netcon
        import win32security as sec
        name, domain, kind = sec.LookupAccountSid(None, sec.ConvertStringSidToSid(sid))
        if kind != sec.SidTypeUser or domain.casefold() != win32api.GetComputerName().casefold():
            raise ValueError("Cần Standard User cục bộ của VM.")
        if win32net.NetUserGetInfo(None, name, 1)["flags"] & win32netcon.UF_ACCOUNTDISABLE:
            raise ValueError("Tài khoản Windows lab đang tắt.")
        admins = sec.LookupAccountSid(None, sec.ConvertStringSidToSid("S-1-5-32-544"))[0]
        groups = win32net.NetUserGetLocalGroups(None, name, win32netcon.LG_INCLUDE_INDIRECT)
        if admins.casefold() in {group.casefold() for group in groups}:
            raise ValueError("Tài khoản dùng thử không được là Administrator.")

    def make_directory(self):
        import win32file
        # CreateDirectory fails when the name already exists; ACL applies at creation.
        win32file.CreateDirectory(str(self.directory), security_attributes(DATA_ACL))

    def write_new(self, name, content):
        import win32file
        handle = win32file.CreateFile(str(self.directory / name), win32file.GENERIC_WRITE, 0,
                                      security_attributes(FILE_ACL), win32file.CREATE_NEW, 0, None)
        try:
            if content:
                win32file.WriteFile(handle, content.encode("utf-8"))
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()

    def check_directory(self):
        path = self.directory
        check_data_acl(path, directory=True)
        for item in path.iterdir():
            if item.name not in MARKERS | {CONFIG_FILE} or not item.is_file():
                raise ValueError("Có mục lạ trong ProgramData. Giữ nguyên để quản trị viên kiểm tra.")
            check_data_acl(item, directory=False)

    @contextmanager
    def manager(self, access):
        handle = self.scm.OpenSCManager(None, None, access)
        try:
            yield handle
        finally:
            self.scm.CloseServiceHandle(handle)

    @contextmanager
    def service(self, access):
        with self.manager(self.scm.SC_MANAGER_CONNECT) as manager:
            service = self.scm.OpenService(manager, SERVICE, access)
            try:
                yield service
            finally:
                self.scm.CloseServiceHandle(service)

    def exists(self):
        import pywintypes
        try:
            with self.service(self.scm.SERVICE_QUERY_CONFIG) as svc:
                self.check_image(svc)
            return True
        except pywintypes.error as error:
            if error.winerror == 1060:
                return False
            raise

    def check_image(self, svc):
        config = self.scm.QueryServiceConfig(svc)
        if (config[0] != self.scm.SERVICE_WIN32_OWN_PROCESS or config[3] != self.image
                or config[7].casefold() != SERVICE_ACCOUNT.casefold()):
            raise ValueError("Tên SCM đã được dùng với cấu hình khác. Giữ nguyên service đó.")

    def create(self):
        scm = self.scm
        with self.manager(scm.SC_MANAGER_CREATE_SERVICE) as manager:
            svc = scm.CreateService(manager, SERVICE, "OpenGuard Kids Lab IPC", scm.SERVICE_QUERY_CONFIG,
                                    scm.SERVICE_WIN32_OWN_PROCESS, scm.SERVICE_DEMAND_START, scm.SERVICE_ERROR_NORMAL,
                                    self.image, None, 0, None, SERVICE_ACCOUNT, None)
            scm.CloseServiceHandle(svc)

    def recovery(self):
        scm = self.scm
        with self.service(scm.SERVICE_CHANGE_CONFIG | scm.SERVICE_QUERY_CONFIG | scm.SERVICE_START) as svc:
            self.check_image(svc)
            scm.ChangeServiceConfig2(svc, scm.SERVICE_CONFIG_FAILURE_ACTIONS, RECOVERY)
            actual = scm.QueryServiceConfig2(svc, scm.SERVICE_CONFIG_FAILURE_ACTIONS)
            if actual["ResetPeriod"] != RECOVERY["ResetPeriod"] or list(actual["Actions"]) != RECOVERY["Actions"]:
                raise ValueError("SCM chưa xác nhận cấu hình phục hồi.")

    def start(self):
        scm = self.scm
        with self.service(scm.SERVICE_QUERY_CONFIG | scm.SERVICE_START | scm.SERVICE_QUERY_STATUS) as svc:
            self.check_image(svc)
            if scm.QueryServiceStatusEx(svc)["CurrentState"] == scm.SERVICE_STOPPED:
                scm.StartService(svc, [])
            self.wait_state(svc, scm.SERVICE_RUNNING)

    def wait_state(self, svc, wanted):
        limit = time.monotonic() + 15
        while self.scm.QueryServiceStatusEx(svc)["CurrentState"] != wanted:
            if time.monotonic() >= limit:
                raise TimeoutError("SCM chưa hoàn tất trong 15 giây.")
            time.sleep(0.2)

    def remove(self):
        import ntsecuritycon as rights
        scm = self.scm
        if not self.exists():
            return
        access = scm.SERVICE_CHANGE_CONFIG | scm.SERVICE_QUERY_CONFIG | scm.SERVICE_STOP | scm.SERVICE_QUERY_STATUS
        with self.service(access | rights.DELETE) as svc:
            self.check_image(svc)
            scm.ChangeServiceConfig(svc, scm.SERVICE_NO_CHANGE, scm.SERVICE_DISABLED, scm.SERVICE_NO_CHANGE,
                                    None, None, 0, None, None, None, None)
            state = scm.QueryServiceStatusEx(svc)["CurrentState"]
            if state != scm.SERVICE_STOPPED:
                if state != scm.SERVICE_STOP_PENDING:
                    scm.ControlService(svc, scm.SERVICE_CONTROL_STOP)
                self.wait_state(svc, scm.SERVICE_STOPPED)
            scm.DeleteService(svc)
        # OpenService can still report ERROR_SERVICE_MARKED_FOR_DELETE with external handles.
        # Leave the protected receipt in place for another Remove attempt in that case.
        if self.exists():
            raise RuntimeError("SCM chưa gỡ service. Giữ biên nhận và thử lại.")

    def cleanup(self):
        self.check_directory()
        for name in ("installed", "removing", LEGACY_OWNER, CONFIG_FILE):
            (self.directory / name).unlink(missing_ok=True)
        self.directory.rmdir()


def install(system, config, parent):
    system.check_sid(config["child_sid"])
    parent.check_device()
    if system.exists() or system.directory.exists():
        raise ValueError("Đã có service hoặc ProgramData. Cần kiểm tra lần cài trước.")
    system.make_directory()
    created = False
    try:
        system.check_directory()
        system.write_new(CONFIG_FILE, json.dumps({**config, "owner_id": parent.owner_id}))
        system.check_directory()
        system.create()
        created = True
        system.recovery()
        system.write_new("installed", "")
        system.check_directory()
    except Exception:
        if created:
            system.remove()
        system.cleanup()
        raise


def remove(system, parent):
    system.check_directory()
    started = system.directory / "removing"
    system.exists()  # Reject an unrelated SCM entry even before requesting server revocation.
    if not (system.directory / "installed").is_file():
        raise ValueError("Không có dấu xác nhận đã cài service trên VM.")
    config = json.loads((system.directory / CONFIG_FILE).read_text(encoding="utf-8"))
    owner_id = config.get("owner_id")
    legacy_owner = system.directory / LEGACY_OWNER
    if owner_id is None and legacy_owner.exists():
        owner_id = json.loads(legacy_owner.read_text(encoding="utf-8"))["owner_id"]
    if owner_id is not None and (type(owner_id) is not int or owner_id <= 0):
        raise ValueError("Mã phụ huynh trong cấu hình không hợp lệ.")
    scope = parent.removal("authorize", owner_id)
    if owner_id is None:
        # Upgrade an old installation only after this parent proves ownership on the pinned server.
        owner_id = parent.owner_id
        system.write_new(LEGACY_OWNER, json.dumps({"owner_id": owner_id}))
    if not started.exists():
        system.write_new("removing", "")
    system.remove()
    if scope == "recorded":
        scope = parent.removal("report_removed", owner_id)
    system.cleanup()
    if scope != "recorded":
        print("Đã gỡ service cục bộ. Máy chủ không có nhật ký của hồ sơ để ghi nhận kết quả gỡ.")
    return scope


@contextmanager
def admin_guard():
    """Serialize local install/start/rollback/remove, including separate Windows sessions."""
    import win32event
    handle = win32event.CreateMutex(security_attributes("O:BAG:BAD:P(A;;GA;;;BA)(A;;GA;;;SY)"),
                                    False, r"Global\OpenGuardKidsLab-Setup-v1")
    try:
        if win32event.WaitForSingleObject(handle, 0) not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise ValueError("Một thao tác quản trị lab khác đang chạy.")
        try:
            yield
        finally:
            win32event.ReleaseMutex(handle)
    finally:
        handle.Close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["Preview", "Install", "Start", "Remove", "Rollback"])
    parser.add_argument("--sid")
    parser.add_argument("--child", type=int)
    parser.add_argument("--device")
    parser.add_argument("--origin")
    parser.add_argument("--pin")
    parser.add_argument("--ca", type=Path)
    args = parser.parse_args()
    system = WindowsService()
    if args.action == "Preview":
        print(f"{SERVICE}\n{system.image}\n{system.directory}\n{SERVICE_ACCOUNT}, demand-start")
        return
    import ctypes
    from win32com.shell import shell, shellcon
    if not sys.flags.isolated or not ctypes.windll.shell32.IsUserAnAdmin():
        parser.exit(1, "Dùng service.ps1 trong VM từ PowerShell Administrator.\n")
    programs = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, 0, 0)).resolve()
    if (ROOT != programs / "OpenGuardKidsLab" or Path(sys.executable).resolve() != ROOT / ".venv" / "Scripts" / "python.exe"
            or not Path(sys.base_prefix).resolve().is_relative_to(programs)):
        parser.exit(1, "Cần bản cài và Python được bảo vệ trong Program Files của VM.\n")
    if not sys.stdin.isatty():
        parser.exit(1, "Cần terminal tương tác để xác nhận và nhập mật khẩu cục bộ.\n")
    if input(f"VM lab. Nhập '{args.action} {SERVICE}': ") != f"{args.action} {SERVICE}":
        parser.exit(1, "Đã hủy.\n")
    with admin_guard():
        perform(system, args)


def perform(system, args):
    if args.action == "Install":
        config = enrollment(args.sid or "", args.child or 0, args.device or "", args.origin or "", args.pin or "",
                            args.ca.read_text(encoding="ascii") if args.ca else "")
        with ParentSession(config) as parent:
            install(system, config, parent)
    elif args.action == "Rollback":
        system.check_directory()
        if (system.directory / "installed").exists() or (system.directory / "removing").exists():
            raise ValueError("Một lần cài hoàn tất chỉ được xử lý qua Remove.")
        system.remove()
        system.cleanup()
    else:
        system.check_directory()
        if not (system.directory / "installed").is_file():
            raise ValueError("Lần cài đặt chưa hoàn tất. Kiểm tra Rollback.")
        if args.action == "Start":
            if (system.directory / "removing").exists():
                raise ValueError("Đang gỡ service.")
            system.start()
        else:
            config = json.loads((system.directory / CONFIG_FILE).read_text(encoding="utf-8"))
            with ParentSession(config) as parent:
                remove(system, parent)


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Máy chủ từ chối thao tác (HTTP {error.code}).")
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
