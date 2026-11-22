import os
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session

from .security import Vault

ROOT = Path(__file__).resolve().parent.parent


class Base(DeclarativeBase):
    pass


def next_public_id(context) -> int:
    """Allocate a permanent integer in the same transaction as the new resource."""
    name = context.compiled.statement.table.name
    return context.connection.execute(text(
        "UPDATE public_id_counters SET last_id = last_id + 1 WHERE resource = :name RETURNING last_id"
    ), {"name": name}).scalar_one()


def default_url() -> str:
    return os.environ.get("OGK_DATABASE_URL", f"sqlite:///{(ROOT / 'var' / 'openguard.sqlite3').as_posix()}")


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError("Cấu hình hiện tại chỉ hỗ trợ SQLite.")
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, hide_parameters=True, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA secure_delete=ON")
        finally:
            cursor.close()

    return engine


def migrate(database_url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "server" / "migrations"))
    cfg.attributes["database_url"] = database_url
    command.upgrade(cfg, "head")


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as db:
        yield db


def get_vault(request: Request) -> Vault:
    return request.app.state.vault
