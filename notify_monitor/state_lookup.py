"""Read-only lookups against the bot's canonical state DB.

The monitor needs two facts from ``db/state/state.db`` to push a scenario onto
the worker queue, and neither lives in the monitor's own SQLite:

* **nickname -> gamer.id** — the queue's ``player_id`` is the numeric gamer id,
  but a push notification only carries the player's nickname.
* **adb serial -> device name** — the queue key is ``wos:queue:<instance_id>``
  where ``instance_id`` is the device *name*; the monitor is configured with the
  device's adb serial.

All helpers open the DB read-only and degrade to ``None`` on any error (missing
file, locked DB, schema drift) so a notification is never dropped just because
the lookup failed — the caller logs and skips the push.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from . import config
from .logging_setup import get_logger

if TYPE_CHECKING:
    from pathlib import Path

log = get_logger("state_lookup")


def _connect(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        log.warning("state DB not found at %s; cannot resolve queue ids", db_path)
        return None
    try:
        # uri=True + mode=ro: never create or write the bot's DB from here.
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        log.warning("state DB open failed (%s): %s", db_path, exc)
        return None


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
    conn = _connect(db_path or config.STATE_DB_PATH)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT player_id FROM gamers "
            "WHERE game = ? "
            "AND lower(json_extract(state_json, '$.nickname')) = lower(?) "
            "LIMIT 1",
            (game, nick),
        ).fetchone()
    except sqlite3.Error as exc:
        log.warning("gamer lookup failed for %r: %s", nick, exc)
        return None
    finally:
        conn.close()
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
    conn = _connect(db_path or config.STATE_DB_PATH)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT name FROM devices WHERE adb_serial = ? LIMIT 1",
            (serial,),
        ).fetchone()
    except sqlite3.Error as exc:
        log.warning("device lookup failed for %r: %s", serial, exc)
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return str(row[0])
