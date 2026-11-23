"""Measure the Windows tray/session workers on a disposable paired profile with synthetic inputs."""
import argparse
import csv
import itertools
import json
import math
import multiprocessing as mp
import platform
import secrets
import ssl
import tempfile
import time
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path


def worker(folder, origin, pin, upstream, stop, connection):
    import tkinter as tk
    from tkinter import ttk
    from unittest.mock import patch
    from agent import dns_proxy, tray
    from agent.client import AgentClient
    from agent.policy import Application
    from agent.store import AgentStore

    counts = dict(input_samples=0, foreground_samples=0, wss_connections=0, heartbeat_success=0,
                  domain_events=0, app_segments=0)
    store = AgentStore(folder / "agent.db")
    client = AgentClient(store, origin, pin, str(folder / "ca.pem"))
    real_enqueue, real_sync, real_ws = store.enqueue, client.sync_once, client.transport.socket
    real_listen, real_upstream = dns_proxy.listening, dns_proxy.network_upstream
    active_port = []

    def enqueue(payload, **kwargs):
        result = real_enqueue(payload, **kwargs)
        if payload["type"] == "domain_query":
            counts["domain_events"] += 1
        if payload["type"] == "app_stop":
            counts["app_segments"] += 1
        return result

    def sync(*args, **kwargs):
        answer = real_sync(*args, **kwargs)
        counts["heartbeat_success"] += 1
        return answer

    @contextmanager
    def ws(path, token):
        with real_ws(path, token) as actual:
            counts["wss_connections"] += 1
            if counts["wss_connections"] == 1:
                connection.send(active_port[0])
            yield actual

    @contextmanager
    def listen(resolver, port):
        with real_listen(resolver, port) as bound:
            active_port.append(bound)
            yield bound

    class SyntheticInput:
        sid = "synthetic-lab-identity"

        def __call__(self):
            counts["input_samples"] += 1
            return 0, False

        def console_available(self):
            return True

        def reset_foreground(self):
            pass

        def foreground(self, permitted, needs_hash):
            counts["foreground_samples"] += 1
            return Application("practice.exe", "", self.sid) if permitted() else None

    class SyntheticIcon:
        def __init__(self, show):
            pass

        def present(self):
            return not stop.is_set()

        def close(self):
            pass

    class HiddenWindow(tk.Tk):
        def __init__(self):
            super().__init__()
            self.withdraw()

        def state(self):
            # Simulated visibility applies only to this synthetic profile. Tk stays withdrawn.
            return "normal"

        def mainloop(self):
            controls = [control for frame in self.winfo_children() for control in frame.winfo_children()
                        if isinstance(control, ttk.Checkbutton)]
            if len(controls) != 2:
                raise AssertionError("Expected session and foreground controls")
            for control in controls:
                control.invoke()

            def check_stop():
                if stop.is_set():
                    self.tk.call(self.protocol("WM_DELETE_WINDOW"))
                else:
                    self.after(250, check_stop)
            self.after(250, check_stop)
            super().mainloop()

    with ExitStack() as stack:
        for target, attribute, value in ((store, "enqueue", enqueue), (client, "sync_once", sync),
                (client.transport, "socket", ws), (dns_proxy, "listening", listen),
                (dns_proxy, "network_upstream", lambda address: real_upstream(address, upstream)),
                (tk, "Tk", HiddenWindow), (tray, "TrayIcon", SyntheticIcon)):
            stack.enter_context(patch.object(target, attribute, value))
        tray.run_session(client, input_sample=SyntheticInput(), dns_upstream="127.0.0.1", dns_port=0,
                         lab_foreground=True)
    connection.send(counts)
    connection.close()


def pair(folder, server, pin):
    import httpx
    from sqlalchemy.orm import Session
    from agent.client import AgentClient
    from agent.store import AgentStore
    from server.cli import create_user
    from server.db import make_engine
    from server.models import Child
    from server.security import Vault

    engine = make_engine(server.database)
    password = secrets.token_urlsafe(20)
    try:
        with Session(engine) as db, db.begin():
            parent = create_user(db, "runtime.parent", password)
            child = Child(parent_id=parent.id, name_cipher=Vault(server.key).encrypt("Hồ sơ đo giả"))
            db.add(child)
            db.flush()
            child_id = child.id
    finally:
        engine.dispose()
    ca = str(folder / "ca.pem")
    origin = f"https://127.0.0.1:{server.port}"
    with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=ca), trust_env=False) as parent:
        csrf = {"X-CSRF-Token": parent.get("/api/auth/csrf").raise_for_status().json()["csrf_token"]}
        login = parent.post("/api/auth/login", headers=csrf, json={"username": "runtime.parent", "password": password})
        csrf["X-CSRF-Token"] = login.raise_for_status().json()["csrf_token"]
        consent = {"consent": True, "child_informed": True, "child_agrees": True}
        issued = parent.post(f"/api/children/{child_id}/pairing", headers=csrf, json=consent).raise_for_status()
        AgentClient(AgentStore(folder / "agent.db"), origin, pin, ca).enroll(issued.json()["code"], "Máy đo giả")
    return origin


def sample(process, pair_id, slot, condition, port, requests, delays):
    import dns.message
    import dns.query

    before = process.cpu_times() if process else None
    rss = process.memory_info().rss if process else 0
    started = time.perf_counter()
    for index in range(requests):
        if condition != "idle":
            query = dns.message.make_query(f"exercise-{pair_id}-{index}.example.org", "A")
            sent = time.perf_counter()
            result = dns.query.udp(query, "127.0.0.1", port=port, timeout=2)
            latency = round(1000 * (time.perf_counter() - sent), 3)
            if not result.answer or result.answer[0][0].to_text() != "192.0.2.1":
                raise AssertionError("Synthetic answer mismatch")
            delays.append(dict(pair=pair_id, condition=condition, request=index + 1, latency_ms=latency))
        if process:
            rss = max(rss, process.memory_info().rss)
        time.sleep(max(0, started + (index + 1) / requests - time.perf_counter()))
    ended = time.perf_counter()
    row = dict(pair=pair_id, slot=slot, condition=condition, requests=0 if condition == "idle" else requests,
               elapsed_s=round(ended - started, 6))
    if process is None:
        return row
    after = process.cpu_times()
    cpu_sec = after.user - before.user + after.system - before.system
    row.update(cpu_pct_one_core=round(100 * cpu_sec / (ended - started), 3),
               rss_max_sampled_mib=round(max(rss, process.memory_info().rss) / 1048576, 3))
    return row


def collect(folder, n, requests):
    import psutil
    from agent.dns_proxy import Resolver, listening
    from agent.policy import DomainRules, Scope
    from agent.simulate import demo_policy, example_upstream
    from scripts.benchmark_recovery import LocalServer
    from scripts.lab_certificate import certificates

    pin = certificates(folder)
    server = LocalServer(folder)
    server.start()
    try:
        origin = pair(folder, server, pin)
        synthetic = Resolver(DomainRules(demo_policy()), lambda: Scope(False, False, False), example_upstream, lambda *_: None)
        with listening(synthetic) as upstream:
            context = mp.get_context("spawn")
            stop = context.Event()
            reader, sender = context.Pipe(duplex=False)
            task = context.Process(target=worker, args=(folder, origin, pin, upstream, stop, sender))
            task.start()
            sender.close()
            try:
                if not reader.poll(30):
                    raise TimeoutError("Paired worker did not open its WSS listener")
                proxy = reader.recv()
                process = psutil.Process(task.pid)
                order = list(itertools.permutations(("idle", "direct", "proxy")))
                rows, delays = [], []
                for pair_id in range(1, n + 1):
                    for slot, condition in enumerate(order[(pair_id - 1) % len(order)], 1):
                        rows.append(sample(process, pair_id, slot, condition, proxy if condition == "proxy" else upstream,
                                           requests, delays))
            finally:
                stop.set()
                task.join(12)
                if task.is_alive():
                    task.terminate()
                    task.join(5)
                try:
                    if task.exitcode == 0 and reader.poll(1):
                        counts = reader.recv()
                    else:
                        raise RuntimeError(f"Paired worker exit {task.exitcode}")
                finally:
                    reader.close()
        if min(counts["wss_connections"], counts["heartbeat_success"]) < 2:
            raise AssertionError("Measurement must span the production heartbeat interval")
        if min(counts["domain_events"], counts["app_segments"], counts["foreground_samples"]) < 1:
            raise AssertionError("Expected durable observations and actual session accounting")
        return rows, delays, counts
    finally:
        server.stop()


def stats(values):
    ordered = sorted(values)
    return {label: round(ordered[math.ceil(len(ordered) * q) - 1], 3) for label, q in (("p50", 0.5), ("p95", 0.95), ("max", 1))}


def without_agent(folder, n):
    from agent.dns_proxy import Resolver, listening
    from agent.policy import DomainRules, Scope
    from agent.simulate import demo_policy, example_upstream
    from scripts.benchmark_recovery import LocalServer
    from scripts.lab_certificate import certificates

    certificates(folder)
    server = LocalServer(folder)
    server.start()
    try:
        neutral = Resolver(DomainRules(demo_policy()), lambda: Scope(False, False, False), example_upstream, lambda *_: None)
        rows, delays = [], []
        with listening(neutral) as port:
            for i in range(1, n + 1):
                for slot, qps in enumerate((12, 50) if i % 2 else (50, 12), 1):
                    batch = []
                    row = sample(None, i, slot, "no_agent", port, qps, batch)
                    rows.append(row | {"qps": qps})
                    delays.extend(item | {"qps": qps} for item in batch)
        return rows, delays
    finally:
        server.stop()


def write_csv(path, data):
    with path.open("x", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, data[0].keys())
        writer.writeheader()
        writer.writerows(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", default=30, type=int, choices=range(30, 301), metavar="30..300")
    parser.add_argument("--qps", default=12, type=int, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--no-agent-control", action="store_true",
                        help="Measure the direct path at 12 and 50 requests/s without spawning the agent process.")
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    if args.no_agent_control:
        with tempfile.TemporaryDirectory(prefix="openguard-no-agent-") as tmp:
            rows, delays = without_agent(Path(tmp), args.samples)
        write_csv(output / "no-agent-windows.csv", rows)
        write_csv(output / "no-agent-rtt.csv", delays)
        print(json.dumps({qps: stats([row["latency_ms"] for row in delays if row["qps"] == qps]) for qps in (12, 50)}))
        return
    paths = [output / name for name in ("tray-samples.csv", "tray-dns-latency.csv", "tray-summary.json")]
    if any(path.exists() for path in paths):
        parser.error("Choose a fresh output directory for a new run")
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="openguard-tray-bench-") as tmp:
        rows, delays, counts = collect(Path(tmp), args.samples, args.qps)
    for path, data in zip(paths, (rows, delays)):
        write_csv(path, data)
    summary = dict(started_utc=started, windows=platform.version(), python=platform.python_version(),
                   pairs=args.samples, offered_qps=args.qps, window_s=1, counts=counts)
    for condition in ("idle", "direct", "proxy"):
        subset = [r for r in rows if r["condition"] == condition]
        summary[condition] = {key: stats([r[key] for r in subset]) for key in ("cpu_pct_one_core", "rss_max_sampled_mib")}
        if condition != "idle":
            summary[condition]["request_rtt_ms"] = stats([r["latency_ms"] for r in delays if r["condition"] == condition])
    with paths[-1].open("x", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
