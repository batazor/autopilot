"""Read-only lookups against the bot's canonical state DB.

The monitor needs two facts from ``db/state/state.db`` to push a scenario onto
the worker queue, and neither lives in the monitor's own SQLite:

* **nickname -> gamer.id** — the queue's ``player_id`` is the numeric gamer id,
  but a push notification only carries the player's nickname.
* **adb serial -> device name** — the queue key is ``wos:queue:<instance_id>``
  where ``instance_id`` is the device *name*; the monitor is configured with the
  device's adb serial.

These read through a SQLAlchemy engine opened ``mode=ro`` so this side never
creates or writes the bot's DB. All helpers
degrade to ``None`` on any error (missing file, locked DB, schema drift) so a
notification is never dropped just because the lookup failed — the caller logs
and skips the push.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from . import config
from .logging_setup import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

log = get_logger("state_lookup")

_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


def _engine(db_path: Path) -> Engine | None:
    """Return a cached read-only, key-unlocked engine for ``db_path``.

    Returns ``None`` (and logs) if the file is missing — the lookups treat that
    as "can't resolve" rather than an error.
    """
    if not db_path.exists():
        log.warning("state DB not found at %s; cannot resolve queue ids", db_path)
        return None
    key = str(db_path)
    with _engines_lock:
        engine = _engines.get(key)
        if engine is None:
            # `creator` opens the file ourselves with mode=ro so this side never
            # creates -wal/-shm or mutates the worker's DB from the monitor side.
            def _open() -> sqlite3.Connection:
                return sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=2.0
                )

            engine = create_engine("sqlite://", creator=_open)

            _engines[key] = engine
        return engine


def resolve_player_id(
    nickname: str, game: str, *, db_path: Path | None = None
) -> str | None:
    """Return the numeric ``gamer.id`` whose nickname matches, as a string.

    Match is case-insensitive and exact on the JSON ``nickname`` field. Returns
    ``None`` when the nickname is empty, unknown, or the DB is unavailable.
    """
    nick = (nickname or "").strip()
    if not nick:
        return None
    engine = _engine(db_path or config.STATE_DB_PATH)
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT player_id FROM gamers "
                    "WHERE game = :game "
                    "AND lower(json_extract(state_json, '$.nickname')) = lower(:nick) "
                    "LIMIT 1"
                ),
                {"game": game, "nick": nick},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("gamer lookup failed for %r: %s", nick, exc)
        return None
    if not row or row[0] is None:
        return None
    return str(row[0])


def resolve_instance_id(adb_serial: str, *, db_path: Path | None = None) -> str | None:
    """Return the device ``name`` (queue instance_id) for an adb serial.

    Returns ``None`` when the serial is empty or has no matching device row.
    """
    serial = (adb_serial or "").strip()
    if not serial:
        return None
    engine = _engine(db_path or config.STATE_DB_PATH)
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name FROM devices WHERE adb_serial = :serial LIMIT 1"),
                {"serial": serial},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("device lookup failed for %r: %s", serial, exc)
        return None
    if not row or not row[0]:
        return None
    return str(row[0])
