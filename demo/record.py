"""Record a private, synthetic family session in a purpose-created Edge window."""
import argparse
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
import uvicorn

from agent.client import AgentClient
from agent.policy import Application, Scope, app_decision, executable_hash
from agent.screen_time import CounterStore, ScreenClock
from agent.simulate import simulate
from agent.store import AgentStore
from server.accounts import create_user
from server.db import make_engine, migrate
from server.demo_history import seed_history
from server.main import create_app
from server.models import Child
from server.security import Vault

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = b"OpenGuard Kids synthetic executable fixture (not executable)."


def run_agent(action):
    work = Path(os.environ["OGK_FILM_TEMP"])
    store = AgentStore(work / "agent.sqlite3")
    client = AgentClient(store, os.environ["OGK_CHECK_ORIGIN"])
    if action == "enroll":
        client.enroll(sys.stdin.readline().strip(), "Máy mô phỏng")
        client.sync_once()
        return
    identity = store.identity()
    did = identity["device_id"]
    if action == "wipe":
        assert client.sync_once().get("erased") and store.identity() is None
        return
    if action == "history":
        engine = make_engine(os.environ["OGK_DATABASE_URL"])
        with Session(engine) as db, db.begin():
            seed_history(db, Vault(os.environ["OGK_SECRET_KEY"]), UUID(did))
        engine.dispose()
        return
    if action == "ack":
        counter = CounterStore(store.path, did)
        clock = ScreenClock(counter.load())
        client.sync_once("visible", 180, lambda cmd: counter.apply_command(cmd, clock, time.time()))
        assert counter.load().extra_seconds == 900
        print("Thiết bị mô phỏng đã ghi nhận thêm 900 giây và gửi xác nhận cho máy chủ.")
        return
    client.sync_once()
    identity = store.identity()
    policy = store.read_policy(did, identity["verification_key"])
    assert policy and policy["weekday_minutes"] == policy["weekend_minutes"] == 3
    print(f"Quy tắc phiên bản {policy['version']} đã được thiết bị kiểm tra chữ ký.")
    simulate(policy, int(time.time()))
    original = work / "notepad.exe"
    original.write_bytes(SAMPLE)
    first = executable_hash(original)
    renamed = original.rename(work / "game.exe")
    second = executable_hash(renamed)
    decision = app_decision(policy, Application(renamed.name, second, "demo"), "demo", Scope(True, True, True))
    assert first == second and decision.action == "block"
    print(f"Tệp mẫu trước khi đổi tên: notepad.exe  SHA-256 {first}")
    print(f"Tệp mẫu sau khi đổi tên: game.exe  SHA-256 {second}")
    print(decision.explain(renamed.name))


def record(rehearse: bool):
    import imageio_ffmpeg
    import json

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        url = f"sqlite:///{(work / 'server.sqlite3').as_posix()}"
        key, password = Fernet.generate_key().decode(), secrets.token_urlsafe(24)
        migrate(url)
        app = create_app(url, secret_key=key, secure_cookies=False)
        with app.state.sessions.begin() as db:
            parent = create_user(db, "demo.parent", password)
            profile = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("An (hồ sơ mẫu)"))
            db.add(profile)
            db.flush()
            create_user(db, "demo.child", password, "child-view", profile.id)
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Cần Node.js, Playwright và Microsoft Edge để quay demo.")
        modules = os.environ.get("NODE_PATH") or str(Path(node).parents[1] / "node_modules")
        # Use the imageio wheel's FFmpeg in this recording workspace only.
        package = Path(modules) / "playwright-core" / "browsers.json"
        revision = next(b["revision"] for b in json.loads(package.read_text())["browsers"] if b["name"] == "ffmpeg")
        binary = work / "pw" / f"ffmpeg-{revision}" / "ffmpeg-win64.exe"
        binary.parent.mkdir(parents=True)
        shutil.copyfile(ffmpeg, binary)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, proxy_headers=False))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                for _ in range(150):
                    if server.started:
                        break
                    time.sleep(.1)
                if not server.started:
                    raise RuntimeError("Máy chủ quay demo chưa sẵn sàng.")
                env = dict(os.environ, OGK_FILM_TEMP=tmp, OGK_FILM_PYTHON=sys.executable, OGK_SECRET_KEY=key,
                    OGK_DATABASE_URL=url, OGK_CHECK_PASSWORD=password, OGK_REHEARSE=str(int(rehearse)),
                    OGK_CHECK_ORIGIN=f"http://127.0.0.1:{listener.getsockname()[1]}", NODE_PATH=modules,
                    PLAYWRIGHT_BROWSERS_PATH=str(work / "pw"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                    OGK_FIXTURE_HASH=hashlib.sha256(SAMPLE).hexdigest())
                subprocess.run([node, "demo/record.cjs"], cwd=ROOT, env=env, check=True, timeout=445)
            finally:
                server.should_exit = True
                thread.join(10)
        if rehearse:
            print("Rehearsal passed.")
            return
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "capture.webm", "-i", "timing.srt",
                "-t", (work / "duration.txt").read_text().strip(), "-map", "0:v", "-map", "1:s", "-vf",
                "pad=iw:ih+106:0:0:color=0x15242c,subtitles=timing.srt:force_style='FontName=Arial,FontSize=9,"
                "PrimaryColour=&H00ffffff,Outline=0,Shadow=0,MarginL=12,MarginR=12,MarginV=7'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "22",
                "-c:s", "mov_text", "-disposition:s:0", "0", "-metadata:s:s:0", "language=vie",
                "-movflags", "+faststart", "final.mp4"]
        subprocess.run(args, cwd=work, check=True, timeout=150)
        shutil.copyfile(work / "final.mp4", ROOT / "demo" / "video.mp4")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["record", "rehearse", "enroll", "simulate", "history", "ack", "wipe"])
    action = parser.parse_args().action
    if action in {"record", "rehearse"}:
        if os.environ.get("OGK_FILM_SNAPSHOT"):
            record(action == "rehearse")
        else:
            # Keep the running server, agent and static assets on the same workspace revision.
            local = ROOT / ".local"
            local.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=local, prefix="recording-") as folder:
                snapshot = Path(folder)
                for name in ("server", "dashboard", "shared", "agent"):
                    shutil.copytree(ROOT / name, snapshot / name, ignore=shutil.ignore_patterns("__pycache__"))
                (snapshot / "demo").mkdir()
                for name in ("alembic.ini", "demo/record.py", "demo/record.cjs"):
                    shutil.copyfile(ROOT / name, snapshot / name)
                env = dict(os.environ, OGK_FILM_SNAPSHOT="1")
                steps = ["rehearse", "record"] if action == "record" else ["rehearse"]
                for step in steps:
                    subprocess.run([sys.executable, "-m", "demo.record", step], cwd=snapshot, env=env, check=True)
                if action == "record":
                    shutil.copyfile(snapshot / "demo" / "video.mp4", ROOT / "demo" / "video.mp4")
    else:
        run_agent(action)
