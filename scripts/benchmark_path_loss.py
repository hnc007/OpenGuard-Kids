"""Test a disconnected agent path while the real pinned loopback server stays healthy."""
import argparse
import csv
import json
import socket
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.client import AgentClient
from agent.store import AgentStore
from scripts.benchmark_agent_runtime import pair, stats
from scripts.benchmark_recovery import LocalServer, enqueue
from scripts.lab_certificate import certificates
from server.db import make_engine
from server.sync_models import UsageEvent


def run(folder, samples):
    pin = certificates(folder)
    service = LocalServer(folder)
    service.start()
    engine = make_engine(service.database)
    rows = []
    try:
        origin = pair(folder, service, pin)
        store = AgentStore(folder / "agent.db")
        agent = AgentClient(store, origin, pin, str(folder / "ca.pem"))
        agent.sync_once()
        identity = store.identity()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as disconnected:
            # Bound and deliberately not listening. Only the synthetic agent's destination is substituted.
            disconnected.bind(("127.0.0.1", 0))
            for trial in range(1, samples + 1):
                enqueue(store)
                started = time.perf_counter()
                with patch.object(agent.transport, "port", disconnected.getsockname()[1]):
                    try:
                        agent.sync_once()
                    except ConnectionRefusedError:
                        pass
                    else:
                        raise AssertionError("Expected an actual TCP refusal")
                detect_ms = round(1000 * (time.perf_counter() - started), 3)
                if agent.transport.request("GET", "/health").get("status") != "ok":
                    raise AssertionError("Control path failed")
                queued = len(store.batch())
                cached = store.read_policy(identity["device_id"], identity["verification_key"])
                if queued != 1 or cached is None:
                    raise AssertionError("Offline state lost")
                started = time.perf_counter()
                agent.sync_once()
                recovery_ms = round(1000 * (time.perf_counter() - started), 3)
                with Session(engine) as db:
                    committed = db.scalar(select(func.count(UsageEvent.id)))
                if committed != trial or store.batch():
                    raise AssertionError("Recovery did not commit each event exactly once")
                rows.append(dict(sample=trial, fault="tcp_refused", control_health="ok", offline_queued=queued,
                                 offline_policy=cached["version"], offline_detect_ms=detect_ms,
                                 recovery_ms=recovery_ms, server_events=committed))
        return rows
    finally:
        service.stop()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, choices=range(30, 301), metavar="30..300", default=30)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openguard-path-") as folder:
        rows = run(Path(folder), args.samples)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", newline="", encoding="utf-8") as stream:
        w = csv.DictWriter(stream, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({key: stats([r[key] for r in rows]) for key in ("offline_detect_ms", "recovery_ms")}))
