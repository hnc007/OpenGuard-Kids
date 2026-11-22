"""Notification-area application for a paired development profile on Windows."""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import os
import queue
import sqlite3
import sys
import threading
import time
import webbrowser
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

from agent.client import AgentClient, device_process, erasure_message
from agent.coordinator import DemoSession, LabDns, RestNotice
from agent.store import AgentStore


class Visibility:
    """A stalled or closed user interface must not authorise another sample/command."""
    def __init__(self, mono=time.monotonic):
        self.mono, self.deadline = mono, 0

    def update(self, shown):
        self.deadline = self.mono() + 1.5 if shown else 0

    def __call__(self):
        return self.mono() < self.deadline


class RestWindows:
    """Keep one dismissible window per rest episode. Pauses release it immediately."""
    def __init__(self, create):
        self.create, self.window = create, None
        self.current_episode = self.dismissed_episode = None

    def dismiss(self):
        self.dismissed_episode = self.current_episode
        self.update(None)

    def update(self, notice):
        if notice is None or self.dismissed_episode == notice.episode:
            if self.window:
                self.window.destroy()
                self.window = None
            self.current_episode = None
            return
        self.current_episode = notice.episode
        if self.window is None:
            self.window = self.create(self.dismiss)
        self.window.refresh(notice)


class RestDialog:
    def __init__(self, parent, dismiss, origin):
        import tkinter as tk
        from tkinter import ttk

        self.view = tk.Toplevel(parent)
        self.view.title("OpenGuard Kids • Giờ nghỉ")
        self.view.protocol("WM_DELETE_WINDOW", dismiss)
        self.view.bind("<Escape>", lambda _: dismiss())
        box = ttk.Frame(self.view, padding=20)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Đến giờ nghỉ của em", font=("Segoe UI", 17)).pack(anchor="w")
        self.time = tk.StringVar(master=self.view)
        self.reason = tk.StringVar(master=self.view)
        ttk.Label(box, textvariable=self.time).pack(anchor="w", pady=(8, 4))
        ttk.Label(box, textvariable=self.reason, wraplength=400).pack(anchor="w", pady=(0, 8))
        ttk.Button(box, text="Xin thêm giờ trong Góc của em",
                   command=lambda: webbrowser.open(origin + "/#requests")).pack(anchor="w", pady=2)
        ttk.Label(box, text="Nếu cần người giúp, em có thể dùng điện thoại gọi 111.",
                  wraplength=400).pack(anchor="w", pady=(10, 2))
        ttk.Button(box, text="Mở trang Tổng đài 111",
                   command=lambda: webbrowser.open("https://tongdai111.vn/")).pack(anchor="w", pady=2)
        ttk.Button(box, text="Đóng lời nhắc", command=dismiss).pack(anchor="w", pady=(10, 0))

    def refresh(self, notice: RestNotice):
        minutes, seconds = divmod(notice.remaining, 60)
        self.time.set(f"{notice.local_time:%H:%M} · Thời gian còn lại hôm nay: {minutes} phút {seconds:02d} giây")
        self.reason.set(notice.explanation())

    def destroy(self):
        self.view.destroy()


class IconId(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("guidItem", ctypes.c_ubyte * 16)]


class TrayIcon:
    def __init__(self, show_panel):
        import win32api
        import win32con
        import win32gui as gui

        self.gui = gui
        self.icon = gui.LoadIcon(0, win32con.IDI_INFORMATION)
        self.callback = win32con.WM_APP + 1
        def on_icon(hwnd, msg, wparam, lparam):
            if lparam in (win32con.WM_LBUTTONUP, win32con.WM_RBUTTONUP):
                show_panel()
            return 0
        klass = gui.WNDCLASS()
        klass.hInstance = win32api.GetModuleHandle(None)
        klass.lpszClassName = f"OpenGuardKidsDevTray{os.getpid()}"
        klass.lpfnWndProc = {self.callback: on_icon}
        atom = gui.RegisterClass(klass)
        self.hwnd = gui.CreateWindow(atom, klass.lpszClassName, 0, 0, 0, 0, 0, 0, 0, klass.hInstance, None)
        flags = gui.NIF_ICON | gui.NIF_MESSAGE | gui.NIF_TIP | gui.NIF_INFO
        self.data = (self.hwnd, 1, flags, self.callback, self.icon, "OpenGuard Kids • Mô phỏng",
                     "Em có thể xem quy tắc và dừng phiên ngay trong cửa sổ.", 5000, "OpenGuard Kids đang mở", 1)
        try:
            gui.Shell_NotifyIcon(gui.NIM_ADD, self.data)
        except OSError:
            gui.DestroyWindow(self.hwnd)
            raise

    def present(self):
        rect = wintypes.RECT()
        icon_id = IconId(ctypes.sizeof(IconId), self.hwnd, 1)
        result = ctypes.windll.shell32.Shell_NotifyIconGetRect(ctypes.byref(icon_id), ctypes.byref(rect))
        return result == 0 and rect.right > rect.left and rect.bottom > rect.top

    def close(self):
        try:
            self.gui.Shell_NotifyIcon(self.gui.NIM_DELETE, (self.hwnd, 1))
        finally:
            self.gui.DestroyWindow(self.hwnd)


def prepare_enrollment(client, previous_session_ended=False) -> bool:
    """Drain a sealed wipe receipt before offering a fresh, voluntary pairing."""
    identity = client.store.identity()
    if identity and identity["origin"] != client.transport.origin:
        raise ValueError("Kho thiết bị thuộc máy chủ khác. Hãy chọn đúng địa chỉ của lần ghép.")
    if identity and not identity.get("erase_pending"):
        return True
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    window.title("OpenGuard Kids • Ghép máy")
    panel = ttk.Frame(window, padding=20)
    panel.pack(fill="both", expand=True)
    prompt = "Nhập mã ghép 8 ký tự mà phụ huynh vừa tạo."
    if previous_session_ended:
        prompt = "Đã ngừng phiên cũ. Phụ huynh có thể xem trạng thái xóa trên dashboard. " + prompt
    status = tk.StringVar(value=prompt)
    ttk.Label(panel, textvariable=status, wraplength=410).pack(anchor="w", pady=6)
    entry = ttk.Entry(panel, show="*", width=20)
    entry.pack(anchor="w")
    result = queue.Queue()
    busy = False
    paired = False

    def launch(operation):
        nonlocal busy
        if busy:
            return
        busy = True
        button.configure(state="disabled")
        def work():
            try:
                result.put((operation(), None))
            except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                result.put((None, error))
        threading.Thread(target=work, daemon=True).start()

    def pair():
        code = entry.get().strip().upper()
        if len(code) != 8:
            status.set("Mã ghép cần đủ 8 ký tự.")
            return
        entry.delete(0, "end")
        launch(lambda: client.enroll(code, "Máy mô phỏng"))

    button = ttk.Button(panel, text="Ghép đôi", command=pair)
    button.pack(anchor="w", pady=6)
    pending = bool(identity)

    def retry_receipt():
        status.set("Dữ liệu trên máy đã xóa. Đang gửi biên nhận đến máy chủ.")
        entry.configure(state="disabled")
        launch(client.retry_pending_receipt)

    def poll():
        nonlocal busy, paired, pending
        if not result.empty():
            value, error = result.get_nowait()
            busy = False
            if error is not None and pending:
                status.set("Đã xóa trên máy. Chưa xác minh được biên nhận với máy chủ. Sẽ thử lại sau một phút.")
                window.after(60000, retry_receipt)
            elif error is not None:
                status.set(f"Chưa ghép được máy. {error}")
                button.configure(state="normal")
            elif pending:
                pending = False
                status.set((erasure_message(value) if value else "Máy chưa có hồ sơ.") + " Có thể ghép lại bằng mã mới.")
                entry.configure(state="normal")
                button.configure(state="normal")
                entry.focus()
            else:
                paired = True
                window.quit()
                return
        window.after(200, poll)

    window.protocol("WM_DELETE_WINDOW", window.quit)
    if pending:
        retry_receipt()
    else:
        entry.focus()
    window.after(200, poll)
    try:
        window.mainloop()
    finally:
        window.destroy()
    return paired


def run(client, input_sample=None, lab_ipc=False, dns_upstream=None, dns_port=53, lab_app=False, lab_rest=False):
    ended = False
    while prepare_enrollment(client, ended):
        if not run_session(client, input_sample, lab_ipc, dns_upstream, dns_port, lab_app, lab_rest):
            return
        ended = True


def run_session(client, input_sample=None, lab_ipc=False, dns_upstream=None, dns_port=53, lab_app=False, lab_rest=False):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from websockets.exceptions import WebSocketException

    lease = Visibility()
    messages = queue.Queue(maxsize=16)
    def notice(message):
        try:
            messages.put_nowait(message)
            return True
        except queue.Full:
            return False
    session = DemoSession(client, lease, notice, input_sample=input_sample)
    window = tk.Tk()
    window.title("OpenGuard Kids • Máy mô phỏng")
    window.minsize(460, 300)
    panel = ttk.Frame(window, padding=20)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="Giờ dùng máy của em", font=("Segoe UI", 17)).pack(anchor="w")
    status = tk.StringVar(value="Đang chuẩn bị khay hệ thống.")
    ttk.Label(panel, textvariable=status, wraplength=420).pack(anchor="w", pady=(8, 4))
    ttk.Label(panel, text="Ứng dụng này ghi nhận gì về em?", font=("Segoe UI", 12)).pack(anchor="w", pady=(10, 2))
    ttk.Label(panel, wraplength=420, text="Đây là máy mô phỏng. Khi em mở phiên, thời gian của ứng dụng mẫu, "
              "cảnh báo và lần thử trang mẫu sẽ hiện trong báo cáo gia đình. Em có thể tạm dừng bất cứ lúc nào.").pack(anchor="w")
    if input_sample:
        ttk.Label(panel, wraplength=420, text="Trong phòng lab, đồng hồ dùng thời điểm tương tác gần nhất "
                  "trên tài khoản thử để tạm dừng khi em đi vắng.").pack(anchor="w", pady=4)
    if dns_upstream:
        ttk.Label(panel, wraplength=420, text="VM thử nghiệm đang dùng bộ lọc DNS cục bộ. "
                  "Báo cáo chỉ lưu miền đăng ký. Các cổng trợ giúp luôn được phép.").pack(anchor="w", pady=4)
    running = tk.BooleanVar(value=False)
    ttk.Checkbutton(panel, variable=running, text="Bắt đầu phiên mô phỏng trên hồ sơ thử nghiệm").pack(anchor="w", pady=6)
    show_rest = tk.BooleanVar(value=False)
    rest = RestWindows(lambda dismiss: RestDialog(window, dismiss, client.transport.origin))
    if lab_rest:
        ttk.Checkbutton(panel, variable=show_rest,
                        text="Hiện cửa sổ nhắc nghỉ khi đến giờ trên tài khoản lab này").pack(anchor="w", pady=2)
    note = tk.StringVar(value="")
    ttk.Label(panel, wraplength=420, textvariable=note).pack(anchor="w")
    recent = None
    if dns_upstream:
        ttk.Label(panel, text="Thông báo gần đây").pack(anchor="w")
        recent = tk.Listbox(panel, height=4, width=62)
        recent.pack(anchor="w", fill="x")
        scroll = ttk.Scrollbar(panel, orient="horizontal", command=recent.xview)
        scroll.pack(fill="x")
        recent.configure(xscrollcommand=scroll.set)
    def try_site():
        if session.lock.acquire(blocking=False):
            try:
                note.set(session.try_domain())
            finally:
                session.lock.release()
        else:
            note.set("Đang đồng bộ. Em thử lại sau giây lát nhé.")
    ttk.Button(panel, text="Thử quy tắc với trang mẫu", command=try_site).pack(anchor="w", pady=4)
    ttk.Button(panel, text="Mở báo cáo và gửi ý kiến",
               command=lambda: webbrowser.open(client.transport.origin + "/")).pack(anchor="w")
    if lab_ipc:
        from agent.lab_service import service_status
        def probe():
            try:
                notice("Service lab đã xác nhận kết nối." if service_status() else "Service lab chưa phản hồi.")
            except (OSError, ValueError):
                notice("Chưa xác thực được kết nối tới service lab.")
        ttk.Button(panel, text="Kiểm tra kết nối service lab",
                   command=lambda: threading.Thread(target=probe, daemon=True).start()).pack(anchor="w")
    active_trial = []
    if lab_app:
        from agent.lab_app import AppTrial, Notepad
        def release_trial(trial):
            trial.process.release()
            active_trial.clear()
        def check_closed(trial, deadline):
            try:
                if session.lock.acquire(blocking=False):
                    try:
                        if trial.confirmed():
                            note.set("Notepad thử nghiệm đã đóng.")
                            release_trial(trial)
                            return
                    finally:
                        session.lock.release()
                if time.monotonic() < deadline:
                    window.after(300, check_closed, trial, deadline)
                    return
                note.set("Notepad có thể đang chờ em lưu bài. Em quyết định khi nào đóng.")
            except (OSError, ValueError, sqlite3.Error):
                note.set("Chưa xác nhận được Notepad đã đóng.")
            release_trial(trial)
        def finish_trial(trial, explanation):
            try:
                approved = messagebox.askokcancel("Quy tắc ứng dụng của em",
                    explanation.explain("Notepad thử nghiệm") + "\n\nEm đồng ý thử đóng cửa sổ trống vừa mở?",
                    parent=window)
                if not approved:
                    release_trial(trial)
                    note.set("Đã hủy phép thử. Em có thể tự đóng Notepad.")
                    return
                if session.lock.acquire(blocking=False):
                    try:
                        closed = trial.finish(explanation)
                    finally:
                        session.lock.release()
                else:
                    closed = False
                note.set("Đã đề nghị Notepad lab đóng. Em vẫn có thể lưu bài." if closed
                         else "Phiên hoặc cửa sổ đã thay đổi. Em có thể tự đóng Notepad lab.")
                if closed:
                    window.after(300, check_closed, trial, time.monotonic() + 5)
                    return
            except (OSError, ValueError, sqlite3.Error):
                note.set("Chưa xác nhận được cửa sổ Notepad lab.")
            release_trial(trial)
        def begin_trial():
            if active_trial or not session.ready() or not input_sample.console_available():
                note.set("Mở phiên lab đã ghép đôi trước khi thử.")
                return
            if not session.lock.acquire(blocking=False):
                note.set("Đang đồng bộ. Em thử lại sau giây lát nhé.")
                return
            try:
                trial = AppTrial(session, input_sample, Notepad())
                active_trial.append(trial)
                matched = trial.blocked()
                if matched:
                    note.set(matched[1].explain(matched[0].name))
                    window.after_idle(finish_trial, trial, matched[1])
                else:
                    trial.process.release()
                    active_trial.clear()
                    note.set("Ứng dụng được phép hoặc Notepad đã chuyển sang cửa sổ khác. Em có thể tự đóng.")
            except (OSError, ValueError, sqlite3.Error):
                if active_trial:
                    active_trial.pop().process.release()
                note.set("Chưa mở được bản Notepad thử nghiệm.")
            finally:
                session.lock.release()
        ttk.Button(panel, text="Mở Notepad trống và thử quy tắc đóng ứng dụng", command=begin_trial).pack(anchor="w")
    stopped = threading.Event()
    workers_started = False
    def show():
        window.deiconify()
        window.lift()
    icon = TrayIcon(show)

    def sample():
        try:
            while not stopped.is_set():
                session.tick()
                stopped.wait(1)
        except (ValueError, OSError, RuntimeError, sqlite3.Error):
            notice("Lỗi dữ liệu cục bộ. Đã dừng mô phỏng.")
            stopped.set()

    def synchronize():
        while not stopped.is_set() and not session.erased:
            deadline = time.monotonic() + 60
            try:
                session.sync()
                if session.erased:
                    break
                if session.ready() and session.available:
                    client.listen_commands(session.command, lambda: session.ready() and session.available, seconds=55)
            except (OSError, RuntimeError, ValueError, WebSocketException, sqlite3.Error):
                session.check_identity()
                notice("Chưa đồng bộ được. Em có thể dùng quy tắc đã xác minh còn hạn.")
            stopped.wait(max(0, deadline - time.monotonic()))

    def update():
        nonlocal workers_started
        if session.erased:
            lease.update(False)
            rest.update(None)
            stopped.set()
            window.quit()
            return
        if stopped.is_set():
            lease.update(False)
            rest.update(None)
            status.set("Phiên đã dừng.")
            return
        if not icon.present():
            lease.update(False)
            rest.update(None)
            if workers_started:
                stopped.set()
            status.set("Đã tạm dừng. Hãy hiển thị biểu tượng OpenGuard Kids trong khay và mở lại ứng dụng.")
        else:
            enabled = running.get() and window.state() == "normal"
            lease.update(enabled)
            rest.update(session.current_lab_rest() if lab_rest and enabled and show_rest.get() else None)
            if enabled and not workers_started:
                workers_started = True
                for task in (sample, synchronize):
                    threading.Thread(target=task, daemon=True).start()
            status.set(session.status if enabled else "Đang tạm dừng phiên mô phỏng.")
        while not messages.empty():
            message = messages.get_nowait()
            note.set(message)
            if recent is not None:
                recent.insert("end", message)
                if recent.size() > 16:
                    recent.delete(0)
                recent.see("end")
        window.after(400, update)

    def close():
        if session.lock.acquire(blocking=False):
            try:
                session.close()
            finally:
                session.lock.release()
        stopped.set()
        lease.update(False)
        rest.update(None)
        window.quit()
    window.protocol("WM_DELETE_WINDOW", close)
    window.after(100, update)
    try:
        with ExitStack() as runtime:
            if dns_upstream:
                from agent.dns_proxy import Resolver, listening, network_upstream
                gate = LabDns(session, input_sample.console_available, notice)
                resolver = Resolver(gate, gate.scope, network_upstream(dns_upstream),
                                    gate.observe, on_allowed=gate.observe)
                runtime.enter_context(listening(resolver, port=dns_port))
            window.mainloop()
    finally:
        lease.update(False)
        stopped.set()
        if active_trial:
            active_trial.pop().process.release()
        rest.update(None)
        icon.close()
        window.destroy()
    return session.erased


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".local/agent.sqlite3"))
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--pin", "--pin-sha256")
    parser.add_argument("--ca", "--ca-file")
    parser.add_argument("--lab-input-sid", metavar="SID", help="Dùng idle và trạng thái khóa của tài khoản lab khớp SID.")
    parser.add_argument("--lab-ipc", action="store_true", help="Hiện nút kiểm tra service lab qua named pipe.")
    parser.add_argument("--lab-dns-upstream", help="IP riêng của resolver lab, VM chỉ có một tài khoản thử đang dùng.")
    parser.add_argument("--lab-dns-port", type=int, default=53)
    parser.add_argument("--lab-app", action="store_true", help="Hiện nút thử quy tắc với một Notepad trống vừa mở.")
    parser.add_argument("--lab-rest", action="store_true", help="Cho phép bật cửa sổ nghỉ tự nguyện trên tài khoản lab.")
    args = parser.parse_args()
    if os.name != "nt":
        parser.exit(2, "Khay này cần Windows 10/11.\n")
    if (args.lab_app or args.lab_rest) and not args.lab_input_sid:
        parser.error("Tùy chọn này cần SID của Standard User lab.")
    if args.lab_dns_upstream:
        try:
            upstream = ipaddress.IPv4Address(args.lab_dns_upstream)
        except ipaddress.AddressValueError:
            parser.error("Cần địa chỉ IPv4 của DNS trong mạng riêng của lab.")
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if (not args.lab_input_sid or not 1 <= args.lab_dns_port <= 65535
                or not any(upstream in ipaddress.IPv4Network(net) for net in networks)):
            parser.error("Proxy cần SID của Standard User, cổng hợp lệ và DNS riêng RFC1918 của lab.")
    try:
        with device_process(args.store):
            input_sample = None
            if args.lab_input_sid:
                from agent.lab_input import LabInput
                input_sample = LabInput(args.lab_input_sid)
            run(AgentClient(AgentStore(args.store), args.server, args.pin, args.ca), input_sample, args.lab_ipc,
                args.lab_dns_upstream, args.lab_dns_port, args.lab_app, args.lab_rest)
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Không mở được phiên. {error}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
