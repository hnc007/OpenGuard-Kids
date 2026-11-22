"""Optional SCM service for a Windows lab. Only a visibility/status pipe lives in Session 0."""
from __future__ import annotations

import ctypes
import os
import re
import threading
import time

if os.name == "nt":
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32security
    import win32service
    import win32serviceutil


PIPE = r"\\.\pipe\OpenGuardKidsLab-v1"
SERVICE = "OpenGuardKidsLab"
CHILD_SID = re.compile(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+")
PIPE_ACCESS = 0x12008B  # generic-read + FILE_WRITE_DATA; excludes FILE_CREATE_PIPE_INSTANCE


def pipe_attributes(child_sid):
    if not CHILD_SID.fullmatch(child_sid):
        raise ValueError("Cần SID của một tài khoản Windows lab cụ thể.")
    dacl = f"D:P(A;;GA;;;SY)(A;;GA;;;LS)(A;;0x12008b;;;{child_sid})"
    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(dacl, 1)
    return sa


def client_sid(handle):
    # Identification-only impersonation. Always revert before writing or invoking service code.
    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token = win32security.OpenThreadToken(win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            return win32security.ConvertSidToStringSid(sid)
        finally:
            token.Close()
    finally:
        win32security.RevertToSelf()


def serve_pipe(stop, child_sid, name=PIPE):
    mode = win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_NOWAIT | 8
    handle = win32pipe.CreateNamedPipe(name, win32pipe.PIPE_ACCESS_DUPLEX | 0x80000,
                                       mode, 1, 64, 64, 200, pipe_attributes(child_sid))
    try:
        while not stop.is_set():
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as error:
                if error.winerror == 536:  # ERROR_PIPE_LISTENING
                    stop.wait(0.1)
                    continue
                if error.winerror != 535:  # ERROR_PIPE_CONNECTED
                    raise
            try:
                deadline = time.monotonic() + 1
                while not stop.is_set() and time.monotonic() < deadline:
                    _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
                    if available:
                        if available > 32:
                            break
                        _, data = win32file.ReadFile(handle, 32)
                        if client_sid(handle) == child_sid and data == b"STATUS":
                            win32file.WriteFile(handle, b"LAB-STATUS-ONLY")
                            # Disconnecting before the client reads discards pending
                            # bytes. A bounded close handshake avoids FlushFileBuffers.
                            while not stop.is_set() and time.monotonic() < deadline:
                                _, pending, _ = win32pipe.PeekNamedPipe(handle, 0)
                                if pending:
                                    break
                                stop.wait(0.02)
                        break
                    stop.wait(0.02)
            except pywintypes.error as error:
                if error.winerror not in {5, 109, 232, 233, 234, 1368}:
                    raise
            finally:
                win32pipe.DisconnectNamedPipe(handle)
    finally:
        handle.Close()


def service_status():
    """Test only a running SCM instance. A process claiming the pipe name cannot impersonate it."""
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(scm, SERVICE, win32service.SERVICE_QUERY_STATUS)
        try:
            status = win32service.QueryServiceStatusEx(service)
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(scm)
    if status["CurrentState"] != win32service.SERVICE_RUNNING:
        raise ValueError("Service lab chưa chạy.")
    # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION prevents a server using
    # the tray user's token to perform operations as that user.
    handle = win32file.CreateFile(PIPE, PIPE_ACCESS, 0, None, win32con.OPEN_EXISTING, 0x110000, None)
    try:
        pid = ctypes.c_ulong()
        if (not ctypes.windll.kernel32.GetNamedPipeServerProcessId(ctypes.c_void_p(int(handle)),
                                                                  ctypes.byref(pid))
                or pid.value != status["ProcessId"]):
            raise ValueError("Không xác nhận được PID của service.")
        win32file.WriteFile(handle, b"STATUS")
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
            if available:
                _, data = win32file.ReadFile(handle, 32)
                win32file.WriteFile(handle, b"OK")
                return data == b"LAB-STATUS-ONLY"
            time.sleep(0.03)
        return False
    finally:
        handle.Close()


if os.name == "nt":
    class LabService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE
        _svc_display_name_ = "OpenGuard Kids • Lab IPC"

        def __init__(self, args):
            super().__init__(args)
            self.stop = threading.Event()
            self.sid = args[1] if len(args) == 2 else ""

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop.set()

        def SvcDoRun(self):
            serve_pipe(self.stop, self.sid)


if __name__ == "__main__":
    import sys
    if os.name != "nt":
        raise SystemExit("Cần Windows.")
    if sys.argv[1:] == ["--scm"]:
        import servicemanager
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LabService)
        servicemanager.StartServiceCtrlDispatcher()
    elif sys.argv[1:] == ["--status"]:
        raise SystemExit(0 if service_status() else 1)
    else:
        print("Chế độ lab: --scm do Windows SCM gọi; --status để kiểm tra IPC từ tài khoản trẻ mô phỏng.")
