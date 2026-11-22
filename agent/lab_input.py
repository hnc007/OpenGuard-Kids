"""Opt-in physical-console lab sampler. Returns only idle duration and an availability flag."""
import os


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
