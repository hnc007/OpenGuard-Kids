"""Enroll a visible simulator and synchronize only its own device partition."""

import argparse
import getpass
import hashlib
import hmac
import http.client
import json
import os
import re
import ssl
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from agent.store import AgentStore


class DeviceHTTPError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Máy chủ trả HTTP {status}.")


@contextmanager
def device_process(store: Path):
    """Keep CLI and tray from overwriting each other's on-disk simulation state."""
    if os.name != "nt":
        yield
        return
    import win32api
    import win32event

    lock_id = hashlib.sha256(str(store.resolve()).casefold().encode()).hexdigest()
    mutex = win32event.CreateMutex(None, True, "Local\\OpenGuardKidsDevTray-" + lock_id)
    try:
        if win32api.GetLastError() == 183:
            raise ValueError("Thiết bị đang mở phiên khác. Hãy dùng khay hoặc đóng khay trước khi chạy sync.")
        yield
    finally:
        mutex.Close()


def erasure_message(result: dict) -> str:
    if result.get("receipt_status") == "unavailable":
        return "Đã xóa dữ liệu trên máy này. Máy chủ không còn biên nhận để xác minh trạng thái từ xa."
    return "Đã xóa dữ liệu trên máy này. Máy chủ đã xác nhận biên nhận."


class PinnedContext(ssl.SSLContext):
    """Verify the chain, hostname and leaf fingerprint before sending credentials."""

    def wrap_socket(self, *args, **kwargs):
        sock = super().wrap_socket(*args, **kwargs)
        actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
        if not any(hmac.compare_digest(actual, pin) for pin in self.pins):
            sock.close()
            raise ssl.SSLError("Chứng chỉ máy chủ không khớp mã ghim.")
        return sock


class Transport:
    def __init__(self, origin: str, pin_sha256: str | None, ca_file: str | None):
        parts = urlsplit(origin)
        if (not parts.hostname or parts.scheme not in {"https", "http"} or parts.path not in {"", "/"}
                or parts.query or parts.fragment or parts.username is not None or parts.password is not None):
            raise ValueError("Địa chỉ máy chủ cần gồm giao thức, tên máy và cổng.")
        self.host, self.port = parts.hostname, parts.port
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.context = None
        if parts.scheme == "https":
            pins = (pin_sha256 or "").lower().split(",")
            if not 1 <= len(pins) <= 2 or any(not re.fullmatch(r"[0-9a-f]{64}", pin) for pin in pins):
                raise ValueError("HTTPS yêu cầu mã SHA-256 chứng chỉ do quản trị viên cung cấp.")
            self.context = PinnedContext(ssl.PROTOCOL_TLS_CLIENT)
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context.pins = tuple(pins)
            self.context.load_default_certs()
            if ca_file:
                self.context.load_verify_locations(cafile=ca_file)
        elif self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("HTTP chỉ được dùng với địa chỉ loopback trong môi trường phát triển.")

    def request(self, method: str, path: str, data=None, token=None):
        if self.context:
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=8, context=self.context)
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=8)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            conn.request(method, path, body=None if data is None else json.dumps(data).encode(), headers=headers)
            result = conn.getresponse()
            if not 200 <= result.status < 300:
                raise DeviceHTTPError(result.status)
            raw = result.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Phản hồi vượt dung lượng cho phép.")
            return json.loads(raw) if raw else {}
        finally:
            conn.close()

    def socket(self, path: str, token: str):
        return connect(self.origin.replace("http", "ws", 1) + path, ssl=self.context, proxy=None,
                       additional_headers={"Authorization": f"Bearer {token}"}, max_size=65536,
                       compression=None, open_timeout=5, close_timeout=1)


class AgentClient:
    def __init__(self, store: AgentStore, base_url: str, pin_sha256=None, ca_file=None):
        self.store = store
        self.transport = Transport(base_url, pin_sha256, ca_file)

    def enroll(self, code: str, label: str):
        self.retry_pending_receipt()
        if self.store.identity():
            raise ValueError("Thiết bị đã có danh tính. Cần hoàn tất gỡ hoặc xóa trước.")
        result = self.transport.request("POST", "/api/enroll",
                                        {"code": code, "label": label, "installation_id": str(uuid4())})
        self.store.save_identity({**result, "origin": self.transport.origin}, new=True)
        return result["device_id"]

    def _identity(self):
        identity = self.store.identity()
        if not identity or identity["origin"] != self.transport.origin:
            raise ValueError("Chưa ghép đôi với máy chủ này.")
        if "erasure_token" not in identity:
            previous = identity.copy()
            identity["erasure_token"] = identity["refresh_token"]
            self.store.save_identity(identity, previous=previous)
        return identity

    def retry_pending_receipt(self) -> dict | None:
        """Finish only a durable wipe receipt, without a heartbeat, policy or event upload."""
        identity = self.store.identity()
        if identity and identity.get("erase_pending"):
            return self._erase_if_requested(self._identity())
        return None

    def _erase_if_requested(self, identity) -> dict | None:
        path = f"/api/devices/{identity['device_id']}/erasure"
        proof = {"erasure_token": identity["erasure_token"], "acknowledge": False}
        if not identity.get("erase_pending"):
            try:
                result = self.transport.request("POST", path, proof)
            except DeviceHTTPError as error:
                if error.status == 404:
                    return None
                raise
            if result.get("erase") is not True:
                raise ValueError("Phản hồi yêu cầu xóa không hợp lệ.")
            self.store.erase(identity, retain_ack=True)
        try:
            response = self.transport.request("POST", path, {**proof, "acknowledge": True})
        except DeviceHTTPError as error:
            if error.status != 404:
                raise
            # Only reached after the committed local wipe. The server may have
            # aged out a delivered receipt after its ACK response was lost.
            self.store.erase(identity, retain_ack=False)
            return {"erased": True, "receipt_status": "unavailable"}
        if response.get("erase") is not True:
            raise ValueError("Máy chủ chưa xác nhận biên nhận xóa.")
        self.store.erase(identity, retain_ack=False)
        return {"erased": True}

    def _refresh(self, identity):
        try:
            tokens = self.transport.request("POST", f"/api/devices/{identity['device_id']}/refresh",
                                            {"refresh_token": identity["refresh_token"]})
        except DeviceHTTPError as error:
            current = self.store.identity()
            if (error.status == 401 and current and not current.get("erase_pending")
                    and current["device_id"] == identity["device_id"]
                    and current["refresh_token"] != identity["refresh_token"]):
                identity.update(current)
                return
            if error.status == 401 and not self._erase_if_requested(identity):
                self.store.erase(identity, retain_ack=True)
                self._erase_if_requested(self.store.identity())
            raise
        previous = identity.copy()
        identity.update(tokens)
        self.store.save_identity(identity, previous=previous)

    def _commands(self, commands, identity, apply_command):
        acknowledged = 0
        if apply_command is None:
            return acknowledged
        for command in commands:
            command_id = str(UUID(command["id"]))
            if command["action"] not in {"lock", "unlock", "add_15"} or int(time.time()) >= command["expires_at"]:
                continue
            # The caller atomically applies the simulated effect and remembers command_id before ACK.
            apply_command(command)
            self.transport.request("POST", f"/api/devices/{identity['device_id']}/commands/{command_id}/ack",
                                   token=identity["access_token"])
            acknowledged += 1
        return acknowledged

    def sync_once(self, status: str = "paused", used_seconds: int = 0, apply_command=None,
                  *, remaining_seconds: int | None = None, simulate_commands: bool = False) -> dict:
        identity = self._identity()
        erased = self._erase_if_requested(identity)
        if erased:
            return erased
        now = int(time.time())
        if identity["access_until"] <= now + 60:
            self._refresh(identity)
        device_id, key = identity["device_id"], identity["verification_key"]
        prefix = f"/api/devices/{device_id}"
        cached = self.store.read_policy(device_id, key)
        reading = {"status": status, "used_seconds": used_seconds, "policy_version": cached["version"] if cached else 0}
        if simulate_commands:
            reading["simulate_commands"] = True
        if status == "visible" and remaining_seconds is not None:
            reading["remaining_seconds"] = remaining_seconds
        try:
            heartbeat = self.transport.request("POST", prefix + "/heartbeat", reading, identity["access_token"])
        except DeviceHTTPError as error:
            if error.status == 401:
                self._refresh(identity)
                heartbeat = self.transport.request("POST", prefix + "/heartbeat", reading, identity["access_token"])
            else:
                raise
        if not cached or cached["version"] != heartbeat["policy_version"] or cached["expires_at"] < now + 86400:
            envelope = self.transport.request("GET", prefix + "/policy", token=identity["access_token"])
            self.store.save_policy(envelope, device_id, key, now=heartbeat["server_time"])
        if simulate_commands:
            from agent.screen_time import CounterStore

            counter = CounterStore(self.store.path, device_id)
            def apply_simulation(command):
                counter.apply_simulation(command, heartbeat["server_time"])
            heartbeat["acknowledged_commands"] = self._commands(heartbeat["commands"], identity, apply_simulation)
        elif status == "visible":
            self._commands(heartbeat["commands"], identity, apply_command)
        # A bounded batch per tick keeps a reconnect from monopolizing the service.
        batch = self.store.batch(now=heartbeat["server_time"], enrollment=identity)
        if batch:
            self.transport.request("POST", prefix + "/events", {"events": batch}, identity["access_token"])
            self.store.acknowledge([event["event_id"] for event in batch])
        return heartbeat

    def listen_commands(self, apply_command, visible, seconds: int = 55):
        """Run between heartbeats; stop promptly if the tray closes or credentials expire."""
        identity = self._identity()
        if not visible() or identity.get("erase_pending") or identity["access_until"] <= int(time.time()):
            return
        deadline = time.monotonic() + min(seconds, 60)
        path = f"/api/devices/{identity['device_id']}/commands/ws"
        with self.transport.socket(path, identity["access_token"]) as channel:
            while visible() and time.monotonic() < deadline and time.time() < identity["access_until"]:
                try:
                    frame = channel.recv(timeout=min(1, max(0.01, deadline - time.monotonic())))
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    break
                if visible():
                    self._commands(json.loads(frame)["commands"], identity, apply_command)


def main():
    parser = argparse.ArgumentParser(description="Ghép đôi hoặc đồng bộ dữ liệu phát triển qua loopback/TLS.")
    parser.add_argument("action", choices=["enroll", "sync"])
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--store", type=Path, default=Path("var/agent.sqlite3"))
    parser.add_argument("--label", default="Máy mô phỏng")
    parser.add_argument("--ca")
    parser.add_argument("--pin")
    args = parser.parse_args()
    try:
        with device_process(args.store):
            client = AgentClient(AgentStore(args.store), args.server, args.pin, args.ca)
            if args.action == "enroll":
                receipt = client.retry_pending_receipt()
                if receipt:
                    print(erasure_message(receipt))
                client.enroll(getpass.getpass("Mã ghép 8 ký tự: ").strip().upper(), args.label)
                print("Đã ghép đôi thiết bị phát triển.")
            result = client.sync_once(simulate_commands=args.action == "sync")
        print(erasure_message(result) if result.get("erased") else f"Đã đồng bộ quy tắc {result['policy_version']}.")
        if args.action == "sync" and not result.get("erased"):
            print(f"Đã xác nhận {result['acknowledged_commands']} lệnh mô phỏng.")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Chưa hoàn tất. {error}\n")


if __name__ == "__main__":
    main()
