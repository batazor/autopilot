"""SQLite persistence for per-gamer state and daily power / level statistics."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml

from config.paths import repo_root
from config.state_schema import GamerState, StateDB

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_LEGACY_YAML_PATH = repo_root() / "db" / "state.yaml"
_path_override: Path | None = None
_conn_lock = threading.RLock()


def default_state_db_path() -> Path:
    return repo_root() / "db" / "state" / "wos.db"


def state_db_path() -> Path:
    return _path_override or default_state_db_path()


def set_state_db_path_for_tests(path: Path | None) -> None:
    global _path_override
    _path_override = path


def legacy_yaml_path() -> Path:
    return _LEGACY_YAML_PATH


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gamers (
    player_id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS player_power_daily (
    player_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    power INTEGER NOT NULL,
    furnace_level INTEGER NOT NULL DEFAULT 0,
    gems INTEGER NOT NULL DEFAULT 0,
    arena_rank INTEGER NOT NULL DEFAULT 0,
    arena_power INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (player_id, day)
);

CREATE TABLE IF NOT EXISTS player_level_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    day TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_level_events_player
    ON player_level_events (player_id, day);

CREATE TABLE IF NOT EXISTS alliance_daily (
    alliance_name TEXT NOT NULL,
    day TEXT NOT NULL,
    power INTEGER NOT NULL DEFAULT 0,
    members_count INTEGER NOT NULL DEFAULT 0,
    members_max INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (alliance_name, day)
);
"""

_PLAYER_POWER_DAILY_COLS = ("gems", "arena_rank", "arena_power")


def _ensure_player_power_daily_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(player_power_daily)")}
    for col in _PLAYER_POWER_DAILY_COLS:
        if col not in existing:
            conn.execute(
                f"ALTER TABLE player_power_daily ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
            )


def _today_iso() -> str:
    return datetime.now(tz=UTC).date().isoformat()


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA_SQL)
        _ensure_player_power_daily_columns(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def load_state_db_raw() -> tuple[StateDB, str | None, str]:
    """Load StateDB from SQLite. Returns (db, parse_error, raw_json_for_debug)."""
    migrate_from_yaml_if_needed()
    try:
        with _conn_lock, _connect() as conn:
            rows = conn.execute(
                "SELECT player_id, state_json FROM gamers ORDER BY player_id"
            ).fetchall()
        gamers: list[GamerState] = []
        for row in rows:
            raw = json.loads(row["state_json"])
            gamers.append(GamerState.model_validate(raw))
        db = StateDB(gamers=gamers)
        return db, None, json.dumps(db.model_dump(mode="json"), indent=2)
    except Exception as exc:
        return StateDB(), f"{type(exc).__name__}: {exc}", ""


def save_state_db(db: StateDB) -> None:
    now = time.time()
    payload = [
        (int(g.id), json.dumps(g.model_dump(mode="json"), separators=(",", ":")), now)
        for g in db.gamers
    ]
    with _conn_lock, _connect() as conn:
        conn.execute("DELETE FROM gamers")
        if payload:
            conn.executemany(
                "INSERT INTO gamers (player_id, state_json, updated_at) VALUES (?, ?, ?)",
                payload,
            )
        conn.commit()


def record_player_stats(gamer: GamerState) -> None:
    """Upsert today's snapshot; record furnace level-up events; mirror alliance row."""
    pid = int(gamer.id)
    power = int(gamer.power or 0)
    furnace_level = int(gamer.buildings.furnace.level or 0)
    gems = int(gamer.gems or 0)
    arena_rank = int(gamer.arena.rank or 0)
    arena_power = int(gamer.arena.myPower or 0)
    alliance_name = (gamer.alliance.name or "").strip()
    alliance_power = int(gamer.alliance.power or 0)
    members_count = int(gamer.alliance.members.count or 0)
    members_max = int(gamer.alliance.members.max or 0)
    day = _today_iso()
    now = time.time()

    with _conn_lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO player_power_daily
                (player_id, day, power, furnace_level, gems,
                 arena_rank, arena_power, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, day) DO UPDATE SET
                power = excluded.power,
                furnace_level = excluded.furnace_level,
                gems = excluded.gems,
                arena_rank = excluded.arena_rank,
                arena_power = excluded.arena_power,
                recorded_at = excluded.recorded_at
            """,
            (pid, day, power, furnace_level, gems, arena_rank, arena_power, now),
        )
        prev = conn.execute(
            """
            SELECT MAX(level) AS max_level FROM player_level_events
            WHERE player_id = ?
            """,
            (pid,),
        ).fetchone()
        prev_level = int(prev["max_level"] or 0) if prev else 0
        if furnace_level > prev_level:
            conn.execute(
                """
                INSERT INTO player_level_events
                    (player_id, level, day, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (pid, furnace_level, day, now),
            )
        if alliance_name:
            conn.execute(
                """
                INSERT INTO alliance_daily
                    (alliance_name, day, power, members_count, members_max, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(alliance_name, day) DO UPDATE SET
                    power = excluded.power,
                    members_count = excluded.members_count,
                    members_max = excluded.members_max,
                    recorded_at = excluded.recorded_at
                """,
                (alliance_name, day, alliance_power, members_count, members_max, now),
            )
        conn.commit()


def get_player_stats(player_id: str) -> dict[str, Any]:
    pid = int(str(player_id).strip())
    with _conn_lock, _connect() as conn:
        gamer_row = conn.execute(
            "SELECT state_json FROM gamers WHERE player_id = ?",
            (pid,),
        ).fetchone()
        series_rows = conn.execute(
            """
            SELECT day, power, furnace_level, gems, arena_rank, arena_power
            FROM player_power_daily
            WHERE player_id = ?
            ORDER BY day ASC
            """,
            (pid,),
        ).fetchall()
        level_rows = conn.execute(
            """
            SELECT day, level
            FROM player_level_events
            WHERE player_id = ?
            ORDER BY day ASC, level ASC
            """,
            (pid,),
        ).fetchall()

    nickname = ""
    if gamer_row:
        try:
            gamer = GamerState.model_validate(json.loads(gamer_row["state_json"]))
            nickname = gamer.nickname or ""
        except Exception:
            logger.debug("get_player_stats: invalid gamer json pid=%s", pid, exc_info=True)

    return {
        "player_id": str(pid),
        "nickname": nickname,
        "series": [
            {
                "day": r["day"],
                "power": int(r["power"]),
                "furnace_level": int(r["furnace_level"]),
                "gems": int(r["gems"] or 0),
                "arena_rank": int(r["arena_rank"] or 0),
                "arena_power": int(r["arena_power"] or 0),
            }
            for r in series_rows
        ],
        "level_events": [
            {"day": r["day"], "level": int(r["level"])}
            for r in level_rows
        ],
    }


def list_alliance_names() -> list[str]:
    with _conn_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT alliance_name FROM alliance_daily ORDER BY alliance_name ASC"
        ).fetchall()
    return [r["alliance_name"] for r in rows if r["alliance_name"]]


def get_alliance_stats(alliance_name: str) -> dict[str, Any]:
    name = str(alliance_name).strip()
    with _conn_lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT day, power, members_count, members_max
            FROM alliance_daily
            WHERE alliance_name = ?
            ORDER BY day ASC
            """,
            (name,),
        ).fetchall()
    return {
        "alliance_name": name,
        "series": [
            {
                "day": r["day"],
                "power": int(r["power"] or 0),
                "members_count": int(r["members_count"] or 0),
                "members_max": int(r["members_max"] or 0),
            }
            for r in rows
        ],
    }


def migrate_from_yaml_if_needed() -> bool:
    """Import db/state.yaml once when SQLite has no gamers."""
    yaml_path = legacy_yaml_path()
    if not yaml_path.is_file():
        return False
    with _conn_lock, _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM gamers").fetchone()
        if count and int(count["n"]) > 0:
            return False
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            db = StateDB.model_validate(raw)
        except Exception:
            logger.exception("YAML → SQLite migration failed: invalid %s", yaml_path)
            return False
        if not db.gamers:
            return False
        save_state_db(db)
        for g in db.gamers:
            record_player_stats(g)
        logger.info(
            "Migrated %d gamer(s) from %s to %s",
            len(db.gamers),
            yaml_path,
            state_db_path(),
        )
        return True
