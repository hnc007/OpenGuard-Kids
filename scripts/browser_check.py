"""Run the browser smoke test with an isolated, synthetic database and a loopback listener."""
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from server.cli import create_user
from server.db import migrate
from server.main import create_app
from server.models import Child


def main():
    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite:///{(Path(directory)/'browser.sqlite3').as_posix()}"
        migrate(url)
        app = create_app(url, secret_key=Fernet.generate_key().decode(), secure_cookies=False)
        password = secrets.token_urlsafe(20)
        with app.state.sessions.begin() as db:
            parent = create_user(db, "browser.parent", password)
            profile = Child(parent_id=parent.id, name_cipher=app.state.vault.encrypt("An (hồ sơ mẫu)"))
            db.add(profile)
            db.flush()
            create_user(db, "browser.child", password, "child-view", profile.id)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, proxy_headers=False))
            runner = threading.Thread(target=server.run, kwargs={"sockets":[listener]},daemon=True)
            runner.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                if not server.started:
                    raise RuntimeError("Máy chủ thử nghiệm chưa sẵn sàng.")
                env=dict(os.environ, OGK_CHECK_ORIGIN=f"http://127.0.0.1:{port}", OGK_CHECK_PASSWORD=password,
                         OGK_CHECK_PYTHON=sys.executable, OGK_CHECK_STORE=str(Path(directory)/"agent.sqlite3"))
                subprocess.run(["node", "scripts/browser_check.cjs"], env=env, check=True, timeout=120)
            finally:
                server.should_exit=True
                runner.join(10)


if __name__ == "__main__":
    main()
