"""Measure dashboard HTTP writes, pinned WSS delivery and durable local receipts on loopback."""
import argparse
import base64
import csv
import json
import math
import os
import platform
import secrets
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from agent.client import AgentClient, Transport
from agent.store import AgentStore
from scripts.lab_certificate import certificates
from server.cli import create_user
from server.db import migrate
from server.main import create_app
from server.models import Child


def milliseconds(start: float) -> float:
    return round(1000 * (time.perf_counter() - start), 3)


def measure(folder: Path, samples: int):
    pin = certificates(folder)
    ca = str(folder / "ca.pem")
    url = f"sqlite:///{(folder / 'server.sqlite3').as_posix()}"
    migrate(url)
    app = create_app(url, secret_key=base64.urlsafe_b64encode(os.urandom(32)).decode(), secure_cookies=True)
    password = secrets.token_urlsafe(18)
    with app.state.sessions.begin() as db:
        parent = create_user(db, "bench.parent", password)
        child = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("Hồ sơ đo"))
        db.add(child)
        db.flush()
        cid = child.id
    config = uvicorn.Config(app, log_level="error", access_log=False, ssl_certfile=str(folder / "server.pem"),
                            ssl_keyfile=str(folder / "server-key.pem"), ws="websockets-sansio", proxy_headers=False)
    runner = uvicorn.Server(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        origin = f"https://127.0.0.1:{sock.getsockname()[1]}"
        thread = threading.Thread(target=runner.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not runner.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Máy chủ TLS thử nghiệm chưa sẵn sàng.")
                time.sleep(0.05)
            return collect(folder, samples, cid, password, origin, ca, pin)
        finally:
            runner.should_exit = True
            thread.join(10)
            if thread.is_alive():
                raise RuntimeError("Máy chủ thử nghiệm chưa dừng.")


def collect(folder, samples, cid, password, origin, ca, pin):
    store = AgentStore(folder / "agent.db")
    agent = AgentClient(store, origin, pin, ca)
    with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=ca), trust_env=False) as parent:
        csrf = {"X-CSRF-Token": parent.get("/api/auth/csrf").raise_for_status().json()["csrf_token"]}
        login = parent.post("/api/auth/login", headers=csrf, json={"username": "bench.parent", "password": password})
        csrf["X-CSRF-Token"] = login.raise_for_status().json()["csrf_token"]
        path = f"/api/children/{cid}"
        pair = parent.post(path + "/pairing", headers=csrf,
                           json={"consent": True, "child_informed": True, "child_agrees": True})
        device = agent.enroll(pair.raise_for_status().json()["code"], "Máy thử hiệu năng")
        agent.sync_once()
        identity = store.identity()
        token = identity["access_token"]
        ws_path = f"/api/devices/{device}/commands/ws"
        results = []
        for origin_client, probe in [
            (Transport(origin, "0" * 64, ca), "https-pin"),
            (Transport(origin, "0" * 64, ca), "wss-pin"),
            (Transport(origin, pin, None), "https-untrusted-ca"),
        ]:
            try:
                if probe == "wss-pin":
                    with origin_client.socket(ws_path, token):
                        raise AssertionError("Máy khách đã chấp nhận mã ghim WSS sai.")
                else:
                    origin_client.request("GET", "/health")
            except ssl.SSLError:
                continue
            raise AssertionError(f"Máy khách chưa chặn cấu hình sai {probe}.")
        version = 1
        # The socket is established before starting the command clock.
        with agent.transport.socket(ws_path, token) as channel:
            for index in range(samples):
                policy_start = time.perf_counter()
                change = parent.put(path + "/policy", headers=csrf, json={"version": version,
                                    "weekday_minutes": 3 + index % 20}).raise_for_status().json()
                agent.sync_once()
                local = store.read_policy(device, identity["verification_key"])
                if local is None or local["version"] != change["version"]:
                    raise AssertionError("Máy thử chưa nhận chính sách.")
                policy_ms = milliseconds(policy_start)
                version = local["version"]
                started = time.perf_counter()
                issued = parent.post(path + f"/devices/{device}/commands", headers=csrf,
                                     json={"action": "unlock"}).raise_for_status().json()
                commands = json.loads(channel.recv(timeout=10))["commands"]
                received_ms = milliseconds(started)
                if not any(cmd["id"] == issued["id"] for cmd in commands):
                    raise AssertionError("Mã lệnh không khớp.")
                agent.transport.request("POST", f"/api/devices/{device}/commands/{issued['id']}/ack", token=token)
                results.append({"sample": index + 1, "policy_cache_ms": policy_ms,
                                "command_received_ms": received_ms, "command_ack_ms": milliseconds(started)})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--out", type=Path, default=Path("docs/evidence"))
    args = parser.parse_args()
    if not 30 <= args.samples <= 300:
        parser.error("Cần từ 30 đến 300 lần đo.")
    with tempfile.TemporaryDirectory(prefix="ogk-sync-") as temp:
        samples = measure(Path(temp), args.samples)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "sync-samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=samples[0].keys())
        writer.writeheader()
        writer.writerows(samples)
    summary = {"scope": "Windows development loopback HTTPS/WSS, immediate heartbeat; same-host DPAPI/SQLite; "
                        "command receipt and server ACK, no Windows action or normal 60-second wait",
               "samples": len(samples), "python": platform.python_version(), "windows": platform.version(),
               "tls_checks": ["HTTPS rejects wrong pin", "WSS rejects wrong pin", "HTTPS rejects untrusted CA"]}
    for metric in ("policy_cache_ms", "command_received_ms", "command_ack_ms"):
        values = sorted(row[metric] for row in samples)
        summary[metric] = {f"p{p}": values[math.ceil(len(values) * p / 100) - 1] for p in (50, 95)}
    (args.out / "sync-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
