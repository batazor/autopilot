"""SQLite data layer.

A new connection is opened per operation (WAL mode, cheap on SQLite) so the
background polling thread and the FastAPI request threads never share a handle.
Writes are serialized through a module-level lock for safety.

Tables
------
players                   id, nickname, game, active, created_at
patterns                  id, game, pattern_regex, event_type, description, active
events                    id, game, player, event_type, raw_text, timestamp
unrecognized_notifications id, game, raw_text, timestamp, reviewed
settings                  key, value      (runtime-tunable config)
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .logging_setup import get_logger

log = get_logger("db")
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname    TEXT NOT NULL,
    game        TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE(nickname, game)
);

CREATE TABLE IF NOT EXISTS patterns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game          TEXT NOT NULL,
    pattern_regex TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game        TEXT NOT NULL,
    player      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unrecognized_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game        TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    reviewed    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_unrec_reviewed ON unrecognized_notifications(reviewed);
"""

DEFAULT_SETTINGS = {
    "poll_interval": str(config.DEFAULT_POLL_INTERVAL),
    "adb_serial": config.DEFAULT_ADB_SERIAL,
    "adb_path": config.DEFAULT_ADB_PATH,
    "monitor_enabled": "1",
}


def connect() -> sqlite3.Connection:
    """Open a fresh WAL-mode connection with a dict-like row factory."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def init_db() -> None:
    """Create tables and seed default settings + seed patterns."""
    with _write_lock, connect() as conn:
        conn.executescript(SCHEMA)
        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, val)
            )
        # Seed patterns only when the table is empty so operator edits survive.
        empty = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 0
        if empty:
            for game in config.GAMES.values():
                for event_type, regex, desc in game.seed_patterns:
                    conn.execute(
                        "INSERT INTO patterns(game, pattern_regex, event_type, description, active) "
                        "VALUES (?,?,?,?,1)",
                        (game.id, regex, event_type, desc),
                    )
            log.info("Seeded default patterns for games: %s", ", ".join(config.GAMES))
        conn.commit()
    log.info("Database ready at %s", config.DB_PATH)


# --- settings --------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_all_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str) -> None:
    with _write_lock, connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()


# --- players ---------------------------------------------------------------

def list_players(game: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM players"
    args: tuple = ()
    if game:
        sql += " WHERE game=?"
        args = (game,)
    sql += " ORDER BY game, nickname"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def add_player(nickname: str, game: str, active: bool = True) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO players(nickname, game, active, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(nickname, game) DO UPDATE SET active=excluded.active",
            (nickname.strip(), game, 1 if active else 0, _now()),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM players WHERE nickname=? AND game=?", (nickname.strip(), game)
        ).fetchone()
        return int(row["id"])


def ensure_player(nickname: str, game: str) -> dict[str, Any] | None:
    """Auto-discover a player. Returns the row, or None for blank nicknames.

    New players are created active=1. Existing rows (incl. deactivated ones)
    are returned untouched so operator toggles are respected.
    """
    nickname = (nickname or "").strip()
    if not nickname:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE nickname=? AND game=?", (nickname, game)
        ).fetchone()
    if row:
        return dict(row)
    add_player(nickname, game, active=True)
    log.info("Auto-discovered player '%s' (%s)", nickname, game)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE nickname=? AND game=?", (nickname, game)
        ).fetchone()
    return dict(row) if row else None


def set_player_active(player_id: int, active: bool) -> None:
    with _write_lock, connect() as conn:
        conn.execute("UPDATE players SET active=? WHERE id=?", (1 if active else 0, player_id))
        conn.commit()


def delete_player(player_id: int) -> None:
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM players WHERE id=?", (player_id,))
        conn.commit()


# --- patterns --------------------------------------------------------------

def list_patterns(game: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM patterns"
    clauses, args = [], []
    if game:
        clauses.append("game=?")
        args.append(game)
    if active_only:
        clauses.append("active=1")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY game, event_type"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def add_pattern(game: str, pattern_regex: str, event_type: str, description: str = "", active: bool = True) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO patterns(game, pattern_regex, event_type, description, active) VALUES (?,?,?,?,?)",
            (game, pattern_regex, event_type, description, 1 if active else 0),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_pattern(pattern_id: int, **fields: Any) -> None:
    allowed = {"game", "pattern_regex", "event_type", "description", "active"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    if "active" in sets:
        sets["active"] = 1 if sets["active"] else 0
    cols = ", ".join(f"{k}=?" for k in sets)
    with _write_lock, connect() as conn:
        conn.execute(f"UPDATE patterns SET {cols} WHERE id=?", (*sets.values(), pattern_id))
        conn.commit()


def delete_pattern(pattern_id: int) -> None:
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM patterns WHERE id=?", (pattern_id,))
        conn.commit()


# --- events ----------------------------------------------------------------

def add_event(game: str, player: str, event_type: str, raw_text: str, timestamp: str | None = None) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO events(game, player, event_type, raw_text, timestamp) VALUES (?,?,?,?,?)",
            (game, player, event_type, raw_text, timestamp or _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_events(limit: int = 100, game: str | None = None, player: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events"
    clauses, args = [], []
    if game:
        clauses.append("game=?")
        args.append(game)
    if player:
        clauses.append("player=?")
        args.append(player)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


# --- unrecognized ----------------------------------------------------------

def add_unrecognized(game: str, raw_text: str, timestamp: str | None = None) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO unrecognized_notifications(game, raw_text, timestamp, reviewed) VALUES (?,?,?,0)",
            (game, raw_text, timestamp or _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_unrecognized(limit: int = 200, include_reviewed: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM unrecognized_notifications"
    if not include_reviewed:
        sql += " WHERE reviewed=0"
    sql += " ORDER BY id DESC LIMIT ?"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]


def set_unrecognized_reviewed(notif_id: int, reviewed: bool = True) -> None:
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE unrecognized_notifications SET reviewed=? WHERE id=?",
            (1 if reviewed else 0, notif_id),
        )
        conn.commit()


def get_unrecognized(notif_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM unrecognized_notifications WHERE id=?", (notif_id,)
        ).fetchone()
    return dict(row) if row else None


def counts() -> dict[str, int]:
    """Summary counts used by the dashboard."""
    with connect() as conn:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])
        return {
            "players": one("SELECT COUNT(*) FROM players"),
            "active_players": one("SELECT COUNT(*) FROM players WHERE active=1"),
            "patterns": one("SELECT COUNT(*) FROM patterns"),
            "events": one("SELECT COUNT(*) FROM events"),
            "unrecognized": one("SELECT COUNT(*) FROM unrecognized_notifications WHERE reviewed=0"),
        }
