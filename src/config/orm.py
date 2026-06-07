"""Shared SQLModel engine for the durable ``state.db``.

``state_sqlite``, ``devices_db`` and ``giftcodes_db`` all persist into one
SQLite file (``state_db_path()``). This module owns a single WAL-mode engine
**per database path** so those modules share one connection pool instead of
opening a fresh ``sqlite3`` connection per call.

Engines are cached by path so tests that point ``state_db_path()`` at a temp
file (via ``set_state_db_path_for_tests``) get an isolated engine.

Each owning module registers its own SQLModel tables and is responsible for
creating them (``SQLModel.metadata.create_all(engine, tables=[...])``) plus any
legacy data migration, guarded so it runs once per engine — see ``ensure_once``.

Schema transforms (legacy column adds, table rebuilds) run through
``apply_migrations``: ordered, namespaced steps recorded once each in a
``_schema_migrations`` table. ``state.db`` is a single file, so a per-file
``PRAGMA user_version`` can't track three modules independently — the tracking
table can, by namespacing step names. Step bodies must stay defensive (a no-op
when their change is already present), because production DBs predate the
tracking table and their changes were applied by the old idempotent code.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlmodel import Session, create_engine

from config import sqlcipher

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

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
            # Defensive: every DB this app owns must be SQLCipher-encrypted, but
            # a plaintext file can still appear out-of-band — created by an
            # external sqlite tool, restored from a hand-made dump, or copied in.
            # The keyed engine below cannot read plaintext pages: every query
            # would fail with SQLCipher's "hmac check failed for pgno=1" and the
            # data would look gone. Encrypt it in place first. Idempotent (no-op
            # on missing/empty/already-encrypted) and keeps a <path>.plaintext.bak.
            if sqlcipher.encrypt_file(path):
                logger.warning(
                    "encrypted plaintext database in place: %s "
                    "(plaintext copy kept at %s.plaintext.bak — delete once verified)",
                    path,
                    path.name,
                )
            engine = create_engine(
                f"sqlite:///{key}",
                module=sqlcipher.DBAPI_MODULE,
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )

            @event.listens_for(engine, "connect")
            def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001 - sqlalchemy hook
                # Unlock the database FIRST — every pragma/read below it needs the
                # decrypted pages. Must precede any other statement on the conn.
                sqlcipher.apply_key_pragmas(dbapi_conn)
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                # Block-and-retry for up to 5s instead of erroring out when another
                # process holds the write lock — the worker runs one process per
                # device, all writing into the shared state.db, so concurrent writes
                # are normal. This is the cross-process safety the in-process locks
                # can't provide.
                cur.execute("PRAGMA busy_timeout=5000")
                # Durable under WAL (only an OS-level power loss can drop the last
                # txn); a large write speedup over the default FULL.
                cur.execute("PRAGMA synchronous=NORMAL")
                # Keep the -wal file from growing unbounded under steady writes.
                cur.execute("PRAGMA wal_autocheckpoint=1000")
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


def apply_migrations(
    engine: Engine,
    namespace: str,
    steps: Sequence[tuple[str, Callable[[sqlite3.Connection], None]]],
) -> None:
    """Run ordered migration ``steps``, each at most once, tracked durably.

    Applied steps are recorded as ``"{namespace}:{name}"`` in a shared
    ``_schema_migrations`` table, so a step runs once per database file ever
    (across process restarts) — unlike ``ensure_once``, which is per-process.

    ``state.db`` is shared by three modules; namespacing the recorded names lets
    each own an independent, ordered history in the one tracking table. Each
    ``fn`` receives a ``sqlite3.Connection`` (``Row`` factory set) and must be
    defensive: a DB that predates this table already has the change applied by
    the old idempotent code, so the first run records the step as a no-op.
    """
    raw = engine.raw_connection()
    try:
        conn = raw.driver_connection
        # driver_connection is a sqlcipher3 connection now, whose cursors only
        # accept the matching Row factory — stdlib sqlite3.Row raises TypeError.
        conn.row_factory = sqlcipher.DBAPI_MODULE.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        applied = {row["name"] for row in conn.execute("SELECT name FROM _schema_migrations")}
        for name, fn in steps:
            full = f"{namespace}:{name}"
            if full in applied:
                continue
            fn(conn)
            conn.execute(
                "INSERT OR IGNORE INTO _schema_migrations (name, applied_at) VALUES (?, ?)",
                (full, time.time()),
            )
            logger.info("applied migration %s", full)
        conn.commit()
    finally:
        raw.close()


def reset_for_tests() -> None:
    """Drop cached engines + init guards. Used by test teardown."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _initialized.clear()
