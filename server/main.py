import asyncio
import ipaddress
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import URL

from . import auth, children
from .db import ROOT, default_url, make_engine
from .models import AuditEntry, LoginSession, LoginWindow
from .retention import FAMILY_HISTORY
from .security import Vault, hash_password

_LOG_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class WebSocketTransport:
    def __init__(self, app, secure: bool):
        self.app = app
        self.secure = secure

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and scope["scheme"] != "wss":
            client = scope.get("client")
            if self.secure or not (client and _loopback(client[0]) and _loopback(URL(scope=scope).hostname)):
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def _private_transport_logs(record: logging.LogRecord) -> bool:
    if record.name == "uvicorn.access":
        return False
    source = "/" + record.pathname.replace("\\", "/").lower()
    transport_record = (record.name == "uvicorn.asgi" or record.name == "websockets.server"
                        or any(folder in source for folder in ("/uvicorn/protocols/", "/websockets/", "/wsproto/")))
    if not transport_record:
        return True
    if record.levelno < logging.WARNING:
        # WebSocket handshakes and ASGI traces can include the request target or body.
        return False
    # Parser errors may quote request data in the exception, its chain, or formatter
    # extras. Keep severity, code location and operational metadata for diagnosis.
    record.msg = f"Chẩn đoán giao thức tại {record.filename}, dòng {record.lineno} ({record.funcName})."
    record.args = ()
    record.exc_info = record.exc_text = record.stack_info = None
    for field in record.__dict__.keys() - _LOG_FIELDS:
        del record.__dict__[field]
    return True


def create_app(database_url: str | None = None, *, secret_key: str | None = None,
               secure_cookies: bool | None = None) -> FastAPI:
    from .family import prune_expired, router as family_router
    from .sync import router as sync_router

    for name in ("uvicorn.access", "uvicorn.error", "uvicorn.asgi", "websockets.server"):
        logging.getLogger(name).addFilter(_private_transport_logs)

    key = secret_key or os.environ.get("OGK_SECRET_KEY")
    if not key:
        raise RuntimeError("Cần đặt OGK_SECRET_KEY trước khi khởi động máy chủ.")
    vault = Vault(key)
    engine = make_engine(database_url or default_url())
    sessions = sessionmaker(engine, expire_on_commit=False)
    secure = os.environ.get("OGK_DEV_HTTP") != "1" if secure_cookies is None else secure_cookies
    cfg = Config(str(ROOT / "alembic.ini"))
    expected_revision = ScriptDirectory.from_config(cfg).get_current_head()

    def ready() -> bool:
        try:
            with engine.connect() as connection:
                return connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_revision
        except SQLAlchemyError:
            return False

    def prune():
        timestamp = int(time.time())
        with sessions.begin() as db:
            db.execute(delete(LoginSession).where(LoginSession.expires_at <= timestamp))
            db.execute(delete(LoginWindow).where(LoginWindow.expires_at <= timestamp))
            db.execute(delete(AuditEntry).where(AuditEntry.ts < timestamp - FAMILY_HISTORY))
            prune_expired(db)

    async def maintenance():
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.to_thread(prune)
            except SQLAlchemyError:
                logging.getLogger(__name__).error("Đợt dọn dữ liệu chưa hoàn tất. Máy chủ sẽ thử lại sau một giờ.")

    @asynccontextmanager
    async def lifespan(app):
        if not ready():
            engine.dispose()
            raise RuntimeError("Database chưa đúng phiên bản. Chạy python -m server.cli migrate.")
        await asyncio.to_thread(prune)
        cleanup = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            engine.dispose()

    app = FastAPI(title="OpenGuard Kids", version="0.1", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.sessions = sessions
    app.state.engine = engine
    app.state.vault = vault
    app.state.secure_cookies = secure
    app.state.dummy_hash = hash_password(secrets.token_urlsafe(32))
    app.add_middleware(WebSocketTransport, secure=secure)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        if request.url.scheme != "https" and (
            secure or not _loopback(request.url.hostname) or not _loopback(request.client.host if request.client else None)
        ):
            return JSONResponse({"detail": "Kết nối này yêu cầu HTTPS."}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(request: Request, exc: RequestValidationError):
        # Pydantic errors can echo invalid password, token or URL input.
        return JSONResponse({"detail": "Dữ liệu gửi lên chưa đúng định dạng. Hãy kiểm tra và thử lại."}, status_code=422)

    @app.get("/health")
    def health():
        if ready():
            return {"status": "ok"}
        return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "dashboard" / "index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "dashboard" / "static"), name="assets")
    for router in (auth.router, children.router, sync_router, family_router):
        app.include_router(router)
    return app
