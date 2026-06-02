"""Shared SQLModel engine for the durable ``state.db``.

``state_sqlite``, ``devices_db`` and ``giftcodes_db`` all persist into one
SQLite file (``state_db_path()``). This module owns a single WAL-mode engine
**per database path** so those modules share one connection pool instead of
opening a fresh ``sqlite3`` connection per call.

Engines are cached by path so tests that point ``state_db_path()`` at a temp
file (via ``set_state_db_path_for_tests``) get an isolated engine.

Each owning module registers its own SQLModel tables and is responsible for
creating them (``SQLModel.metadata.create_all(engine, tables=[...])``) plus any
legacy data migration, guarded so it runs once per engine — see
``ensure_once``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlmodel import Session, create_engine

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Engine

_lock = threading.RLock()
_engines: dict[str, Engine] = {}
_initialized: set[tuple[int, str]] = set()


def get_engine(path: Path) -> Engine:
    """Return the cached WAL-mode engine for ``path`` (created on first use)."""
    key = str(path)
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                f"sqlite:///{key}",
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )

            @event.listens_for(engine, "connect")
            def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001 - sqlalchemy hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

            _engines[key] = engine
        return engine


def session(path: Path) -> Session:
    """Open a short-lived session bound to ``path``'s engine."""
    return Session(get_engine(path))


def ensure_once(engine: Engine, tag: str, setup: Callable[[Engine], None]) -> None:
    """Run ``setup(engine)`` exactly once per (engine, tag).

    ``tag`` namespaces the guard so each module's schema setup runs independently
    on the same shared engine. Thread-safe.
    """
    marker = (id(engine), tag)
    with _lock:
        if marker in _initialized:
            return
        setup(engine)
        _initialized.add(marker)


def reset_for_tests() -> None:
    """Drop cached engines + init guards. Used by test teardown."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _initialized.clear()
