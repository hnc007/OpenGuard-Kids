"""One explicitly launched, blank Windows Notepad instance in the current lab account."""
from __future__ import annotations

import hashlib
from pathlib import Path

from agent.policy import Application, Scope, app_decision


class Notepad:
    def __init__(self):
        import win32api
        import win32con
        import win32file
        import win32process

        self.file = self.process = None
        self.path = (Path(win32api.GetSystemDirectory()) / "notepad.exe").resolve(strict=True)
        try:
            # Keep the file read-locked through the trial. No caller-supplied executable,
            # command-line arguments, inherited handles, or document names.
            self.file = win32file.CreateFile(str(self.path), win32con.GENERIC_READ,
                                             win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, 0, None)
            digest = hashlib.sha256()
            remaining = win32file.GetFileSize(self.file)
            while remaining:
                _, data = win32file.ReadFile(self.file, min(remaining, 1024 * 1024))
                if not data:
                    raise OSError("Không đọc hết tệp ứng dụng lab.")
                digest.update(data)
                remaining -= len(data)
            self.digest = digest.hexdigest()
            self.process, thread, self.pid, _ = win32process.CreateProcess(
                str(self.path), f'"{self.path}"', None, None, False, 0, None,
                str(self.path.parent), win32process.STARTUPINFO())
            thread.Close()
        except BaseException:
            self.release()
            raise

    def snapshot(self, lab):
        import win32con
        import win32process
        import win32security

        if self.exited():
            return None
        image = Path(win32process.GetModuleFileNameEx(self.process, 0)).resolve(strict=True)
        if image != self.path or win32process.GetProcessId(self.process) != self.pid:
            return None
        token = win32security.OpenProcessToken(self.process, win32con.TOKEN_QUERY)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            owner = win32security.ConvertSidToStringSid(sid)
            session = win32security.GetTokenInformation(token, win32security.TokenSessionId)
        finally:
            token.Close()
        if owner != lab.sid or session != lab.session:
            return None
        return Application(self.path.name, self.digest, owner)

    def exited(self):
        import win32event
        return win32event.WaitForSingleObject(self.process, 0) == win32event.WAIT_OBJECT_0

    def ask_to_close(self, still_authorised):
        import win32con
        import win32gui
        import win32process

        windows = []
        def collect(hwnd, _):
            if win32process.GetWindowThreadProcessId(hwnd)[1] == self.pid:
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd).casefold() == "notepad":
                    windows.append(hwnd)
            return True
        win32gui.EnumWindows(collect, None)
        if len(windows) != 1 or not still_authorised():
            return False
        hwnd = windows[0]
        if not win32gui.IsWindow(hwnd) or win32process.GetWindowThreadProcessId(hwnd)[1] != self.pid:
            return False
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    def release(self):
        for handle in (self.process, self.file):
            if handle is not None:
                handle.Close()
        self.file = self.process = None


class AppTrial:
    def __init__(self, session, lab_input, process):
        self.session, self.lab, self.process = session, lab_input, process
        self.requested = None

    def blocked(self):
        session = self.session
        if not session.ready() or not self.lab.console_available():
            return None
        session.load_policy()
        if not session.ready():
            return None
        app = self.process.snapshot(self.lab)
        if app is None:
            return None
        decision = app_decision(session.policy, app, self.lab.sid, Scope(True, True, True))
        return (app, decision) if decision.action == "block" else None

    def finish(self, explained):
        with self.session.lock:
            if not explained:
                return False
            matched = self.blocked()
            if not matched or matched[1] != explained:
                return False
            permitted = lambda: self.blocked() == matched
            if not self.process.ask_to_close(permitted):
                return False
            # WM_CLOSE requests a graceful close. Notepad retains its Save dialog.
            self.requested = matched[0].name
            return True

    def confirmed(self):
        if self.requested and self.process.exited():
            self.session.event("blocked_app", self.requested)
            self.requested = None
            return True
        return False
