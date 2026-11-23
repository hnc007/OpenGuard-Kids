"""Opt-in physical-console lab samplers. Windows handles and image paths never leave the sampler."""
import ctypes
import hashlib
import os
from pathlib import Path, PureWindowsPath

from agent.policy import Application, ESSENTIAL_APPS, HELP_APPS
from shared.protocol import executable


# Browsers and communications clients can provide a route to emergency assistance.
# Blacklisting these program names in a family policy must not generate activity or advice here.
PRIVATE_APPS = ESSENTIAL_APPS | HELP_APPS | {
    "searchhost.exe", "textinputhost.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "applicationframehost.exe",
    "runtimebroker.exe", "securityhealthhost.exe", "pythonw.exe", "python.exe",
}


def image_path(process):
    size = ctypes.c_ulong(32768)
    result = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(int(process)), 0,
                                                           result, ctypes.byref(size)):
        return None
    return result.value


def idle_duration(now_tick: int, last_input_tick: int) -> float:
    # GetLastInputInfo reports DWORD milliseconds, rolling over after ~49.7 days.
    return ((now_tick - last_input_tick) & 0xFFFFFFFF) / 1000


class LabInput:
    def __init__(self, child_sid):
        import win32api
        import win32con
        import win32security
        import win32ts

        from agent.lab_service import CHILD_SID

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            self.sid = win32security.ConvertSidToStringSid(sid)
            groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
            admin = any(win32security.ConvertSidToStringSid(group) == "S-1-5-32-544" for group, _ in groups)
        finally:
            token.Close()
        if not CHILD_SID.fullmatch(child_sid) or self.sid != child_sid or admin:
            raise ValueError("Chế độ lab cần đúng SID tài khoản Standard User dành riêng để thử.")
        self.session = win32ts.ProcessIdToSessionId(os.getpid())
        self.reset_foreground()

    def reset_foreground(self):
        self.last_file = self.last_hash = None

    def _image_hash(self, image, allowed):
        """Only a local executable image from the verified handle; cache its file ID, never a path."""
        import win32file

        path = Path(image)
        if (not path.is_absolute() or len(path.drive) != 2
                or win32file.GetDriveType(path.anchor) != win32file.DRIVE_FIXED):
            return ""
        path = path.resolve(strict=True)
        if (len(path.drive) != 2 or path.suffix.casefold() != ".exe"
                or win32file.GetDriveType(path.anchor) != win32file.DRIVE_FIXED or not allowed()):
            return ""
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if not 2 <= info.st_size <= 64 * 1024 * 1024 or not info.st_ino:
                return ""
            if key == self.last_file:
                return self.last_hash
            if stream.read(2) != b"MZ":
                return ""
            hash_value = hashlib.sha256(b"MZ")
            remaining = info.st_size - 2
            while remaining and allowed():
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    return ""
                remaining -= len(chunk)
                hash_value.update(chunk)
            after = os.fstat(stream.fileno())
            if not remaining and allowed() and (after.st_size, after.st_mtime_ns) == (info.st_size, info.st_mtime_ns):
                self.last_file, self.last_hash = key, hash_value.hexdigest()
                return self.last_hash
        return ""

    def foreground(self, permitted, *, needs_hash):
        """Inspect just the current window's process, under the interactive user's rights."""
        import pywintypes
        import win32api
        import win32con
        import win32gui
        import win32process
        import win32security

        if not permitted() or not self.console_available():
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid or pid == os.getpid():
                return None
            process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
                try:
                    sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
                    if (win32security.ConvertSidToStringSid(sid) != self.sid or
                            win32security.GetTokenInformation(token, win32security.TokenSessionId) != self.session):
                        return None
                finally:
                    token.Close()

                def still_foreground():
                    return (permitted() and self.console_available() and win32gui.GetForegroundWindow() == hwnd
                            and win32process.GetWindowThreadProcessId(hwnd)[1] == pid)

                if not still_foreground():
                    return None
                image = image_path(process)
                if not image:
                    return None
                name = executable(PureWindowsPath(image).name)
                if name in PRIVATE_APPS:
                    return None
                digest = self._image_hash(image, still_foreground) if needs_hash else ""
                return Application(name, digest, self.sid) if still_foreground() else None
            finally:
                process.Close()
        except (pywintypes.error, OSError, ValueError):
            return None

    def console_available(self):
        import pywintypes
        import win32con
        import win32service
        import win32ts

        if self.session == 0 or self.session != win32ts.WTSGetActiveConsoleSessionId():
            return False
        try:
            desktop = win32service.OpenInputDesktop(0, False, win32con.DESKTOP_READOBJECTS)
            try:
                desktop_name = win32service.GetUserObjectInformation(desktop, win32con.UOI_NAME)
            finally:
                del desktop  # PyHDESK owns and closes its desktop handle.
            return desktop_name.casefold() == "default"
        except pywintypes.error:
            return False

    def __call__(self):
        import pywintypes
        import win32api

        if not self.console_available():
            return 0, True
        try:
            last_input = win32api.GetLastInputInfo()
            return idle_duration(win32api.GetTickCount(), last_input), False
        except pywintypes.error:
            # Secure desktop, switch, lock or unavailable input -> suspend the sample.
            return 0, True
