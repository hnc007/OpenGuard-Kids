"""Exercise three isolated failure modes with one synthetic installation on TLS loopback."""
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
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.client import AgentClient
from agent.store import AgentStore
from scripts.lab_certificate import certificates
from server.cli import create_user
from server.db import make_engine, migrate
from server.main import create_app
from server.models import Child
from server.security import Vault
from server.sync_models import UsageEvent


def enqueue(store: AgentStore):
    identity = store.identity()
    store.enqueue({"device_id": identity["device_id"], "child_id": identity["child_id"], "policy_id": 1,
                   "ts": int(time.time()), "duration_sec": 1, "type": "app_stop", "subject": "sample.exe"})


class LocalServer:
    """Start and stop only the Uvicorn instance owned by this measurement."""

    def __init__(self, folder: Path):
        self.database = f"sqlite:///{(folder / 'server.sqlite3').as_posix()}"
        self.key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        self.folder = folder
        self.port = 0
        self.runner = self.thread = None
        migrate(self.database)

    def start(self):
        app = create_app(self.database, secret_key=self.key, secure_cookies=True)
        config = uvicorn.Config(app, log_level="error", access_log=False, proxy_headers=False,
                                ssl_certfile=str(self.folder / "server.pem"), ssl_keyfile=str(self.folder / "server-key.pem"))
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            sock.listen()
            self.port = sock.getsockname()[1]
            self.runner = uvicorn.Server(config)
            self.thread = threading.Thread(target=self.runner.run, kwargs={"sockets": [sock]}, daemon=True)
            self.thread.start()
            limit = time.monotonic() + 15
            while not self.runner.started:
                if not self.thread.is_alive() or time.monotonic() >= limit:
                    raise RuntimeError("Máy chủ thử nghiệm chưa khởi động.")
                time.sleep(0.02)
        except BaseException:
            sock.close()
            raise

    def stop(self):
        if self.thread is not None:
            self.runner.should_exit = True
            self.thread.join(10)
            if self.thread.is_alive():
                raise RuntimeError("Máy chủ thử nghiệm chưa dừng.")
            self.thread = self.runner = None


class DropOneResponse:
    def __init__(self, real):
        self.real = real
        self.origin = real.origin
        self.dropped = False

    def request(self, method, path, data=None, token=None):
        result = self.real.request(method, path, data, token)
        if not self.dropped and path.endswith("/events"):
            self.dropped = True
            raise TimeoutError("Drop the synthetic event receipt after commit")
        return result


def worker(store_path: Path, url: str, ca: str, pin: str, *, write_then_exit: bool):
    store = AgentStore(store_path)
    if write_then_exit:
        enqueue(store)
        # Use a deliberate exit of this disposable worker after the local commit.
        os._exit(23)
    heartbeat = AgentClient(store, url, pin, ca).sync_once()
    if heartbeat["policy_version"] != 1 or store.batch():
        raise RuntimeError("Tiến trình thử chưa phục hồi cache và hàng đợi.")


def run_child(folder: Path, url: str, ca: str, pin: str, action: str, expected: int):
    args = [sys.executable, "-m", "scripts.benchmark_recovery", "--worker", action, "--folder", str(folder),
            "--url", url, "--ca", ca, "--pin", pin]
    job = subprocess.run(args, capture_output=True, timeout=30, check=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if job.returncode != expected:
        raise RuntimeError(f"Tiến trình thử kết thúc với mã {job.returncode}, cần {expected}.")


def run(folder: Path, samples: int) -> list[dict]:
    pin = certificates(folder)
    ca = str(folder / "ca.pem")
    controller = LocalServer(folder)
    controller.start()
    engine = make_engine(controller.database)
    try:
        url = f"https://127.0.0.1:{controller.port}"
        password = secrets.token_urlsafe(18)
        with Session(engine) as db, db.begin():
            parent = create_user(db, "bench.parent", password)
            child = Child(parent_id=parent.id, name_cipher=Vault(controller.key).encrypt("Gia đình giả"))
            db.add(child)
            db.flush()
            child_id = child.id
        store = AgentStore(folder / "agent.db")
        agent = AgentClient(store, url, pin, ca)
        with httpx.Client(base_url=url, verify=ssl.create_default_context(cafile=ca), trust_env=False) as browser:
            csrf = {"X-CSRF-Token": browser.get("/api/auth/csrf").raise_for_status().json()["csrf_token"]}
            login = browser.post("/api/auth/login", headers=csrf, json={"username": "bench.parent", "password": password})
            csrf["X-CSRF-Token"] = login.raise_for_status().json()["csrf_token"]
            consent = {"consent": True, "child_informed": True, "child_agrees": True}
            pair = browser.post(f"/api/children/{child_id}/pairing", headers=csrf, json=consent).raise_for_status()
            agent.enroll(pair.json()["code"], "Thiết bị giả")
        agent.sync_once()
        real = agent.transport
        rows = []
        for fault in ("lost_event_receipt", "server_restart", "agent_process_restart"):
            for i in range(samples):
                if fault == "lost_event_receipt":
                    enqueue(store)
                    agent.transport = DropOneResponse(real)
                    try:
                        agent.sync_once()
                    except TimeoutError:
                        if not agent.transport.dropped:
                            raise
                    else:
                        raise AssertionError("Chưa tái hiện mất phản hồi.")
                    finally:
                        agent.transport = real
                elif fault == "server_restart":
                    controller.stop()
                    enqueue(store)
                    try:
                        agent.sync_once()
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Máy chủ thử nghiệm vẫn trả lời sau khi dừng.")
                else:
                    run_child(folder, url, ca, pin, "write-exit", 23)
                if len(store.batch()) != 1:
                    raise AssertionError("Mất sự kiện trong lúc gián đoạn.")
                started = time.perf_counter()
                if fault == "server_restart":
                    controller.start()
                if fault == "agent_process_restart":
                    run_child(folder, url, ca, pin, "resume", 0)
                else:
                    agent.sync_once()
                recovery_ms = round((time.perf_counter() - started) * 1000, 3)
                with Session(engine) as db:
                    if db.scalar(select(func.count(UsageEvent.id))) != len(rows) + 1 or store.batch():
                        raise AssertionError("Số sự kiện sau khôi phục chưa đúng.")
                rows.append({"fault": fault, "sample": i + 1, "recovery_ms": recovery_ms})
            print(f"{fault}: {samples}/{samples}", flush=True)
        return rows
    finally:
        controller.stop()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--out", type=Path, default=Path("docs/evidence"))
    parser.add_argument("--worker", choices=["write-exit", "resume"], help=argparse.SUPPRESS)
    parser.add_argument("--folder", type=Path, help=argparse.SUPPRESS)
    for name in ("url", "ca", "pin"):
        parser.add_argument("--" + name, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.folder / "agent.db", args.url, args.ca, args.pin, write_then_exit=args.worker == "write-exit")
        return
    if not 30 <= args.samples <= 300:
        parser.error("Cần từ 30 đến 300 mẫu mỗi tình huống.")
    with tempfile.TemporaryDirectory(prefix="ogk-recovery-") as temp:
        rows = run(Path(temp), args.samples)
    args.out.mkdir(exist_ok=True, parents=True)
    with (args.out / "recovery-samples.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    summary = {"samples_per_fault": args.samples, "python": platform.python_version(), "windows": platform.version(),
               "scope": "loopback TLS, idle synthetic agent, immediate retry after fault, no SCM or Windows settings",
               "verification": "one committed event per sample and an empty agent outbox after acknowledgement"}
    for fault in sorted({row["fault"] for row in rows}):
        values = sorted(row["recovery_ms"] for row in rows if row["fault"] == fault)
        summary[fault] = {f"p{p}_ms": values[math.ceil(len(values)*p/100)-1] for p in (50, 95)}
    (args.out / "recovery-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
