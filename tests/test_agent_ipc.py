import os
import threading
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Named Pipe")


def test_pipe_acl_rejects_generic_sid_and_grants_only_configured_sid():
    import win32security as security
    from agent.lab_service import pipe_attributes

    with pytest.raises(ValueError):
        pipe_attributes("S-1-1-0")
    sid = "S-1-5-21-111111-222222-333333-4444"
    acl = pipe_attributes(sid).SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
    rights = {}
    for index in range(acl.GetAceCount()):
        _, mask, who = acl.GetAce(index)
        rights[security.ConvertSidToStringSid(who)] = mask
    assert set(rights) == {"S-1-5-18", "S-1-5-19", sid}
    assert rights[sid] & 2 and not rights[sid] & 4


def test_pipe_status_only_same_user_and_unknown_command():
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32security
    from agent.lab_service import CHILD_SID, PIPE_ACCESS, serve_pipe

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
    finally:
        token.Close()
    sid = win32security.ConvertSidToStringSid(sid)
    if not CHILD_SID.fullmatch(sid):
        pytest.skip("Cần tài khoản Windows cục bộ hoặc domain có SID phù hợp.")
    name = r"\\.\pipe\OpenGuardKids-test-" + uuid4().hex
    stop = threading.Event()
    worker = threading.Thread(target=serve_pipe, args=(stop, sid, name), daemon=True)
    worker.start()
    def request(data):
        deadline = time.monotonic() + 3
        while True:
            try:
                handle = win32file.CreateFile(name, PIPE_ACCESS, 0, None, win32con.OPEN_EXISTING, 0x110000, None)
                break
            except pywintypes.error as error:
                if error.winerror not in (2, 231) or time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
        try:
            win32file.WriteFile(handle, data)
            while time.monotonic() < deadline:
                try:
                    _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
                except pywintypes.error as error:
                    if error.winerror in (109, 232, 233):
                        return b""
                    raise
                if available:
                    _, reply = win32file.ReadFile(handle, 32)
                    win32file.WriteFile(handle, b"OK")
                    return reply
                time.sleep(0.02)
            raise AssertionError("Pipe lab không phản hồi trước thời hạn.")
        finally:
            handle.Close()
    try:
        assert request(b"STATUS") == b"LAB-STATUS-ONLY"
        assert request(b"UNSUPPORTED") == b""
    finally:
        stop.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
