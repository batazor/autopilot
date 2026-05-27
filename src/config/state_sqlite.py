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

from config.paths import repo_root
from config.state_schema import GamerState, StateDB

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_path_override: Path | None = None
_conn_lock = threading.RLock()


_LEGACY_STATE_DB_NAME = "wos.db"
_STATE_DB_NAME = "state.db"


def default_state_db_path() -> Path:
    return repo_root() / "db" / "state" / _STATE_DB_NAME


def state_db_path() -> Path:
    return _path_override or default_state_db_path()


def _maybe_migrate_legacy_db_filename() -> None:
    """Rename ``db/state/wos.db`` → ``db/state/state.db`` if only the old name exists.

    Phase 3: the historical ``wos.db`` is renamed to game-agnostic ``state.db``.
    Existing deployments boot up, detect the legacy file, and atomically rename
    it. Idempotent — does nothing once ``state.db`` exists.
    """
    new_path = default_state_db_path()
    legacy_path = new_path.parent / _LEGACY_STATE_DB_NAME
    if new_path.exists() or not legacy_path.exists():
        return
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.rename(new_path)
        logger.info("migrated legacy state DB: %s → %s", legacy_path, new_path)
    except OSError as exc:
        logger.warning("could not migrate legacy state DB %s: %s", legacy_path, exc)


def set_state_db_path_for_tests(path: Path | None) -> None:
    global _path_override
    _path_override = path


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gamers (
    game TEXT NOT NULL DEFAULT 'wos',
    player_id INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (game, player_id)
);

CREATE TABLE IF NOT EXISTS player_power_daily (
    game TEXT NOT NULL DEFAULT 'wos',
    player_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    power INTEGER NOT NULL,
    furnace_level INTEGER NOT NULL DEFAULT 0,
    gems INTEGER NOT NULL DEFAULT 0,
    arena_rank INTEGER NOT NULL DEFAULT 0,
    arena_power INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (game, player_id, day)
);

CREATE TABLE IF NOT EXISTS player_level_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'wos',
    player_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    day TEXT NOT NULL,
    recorded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_level_events_player
    ON player_level_events (game, player_id, day);

CREATE TABLE IF NOT EXISTS alliance_daily (
    game TEXT NOT NULL DEFAULT 'wos',
    alliance_name TEXT NOT NULL,
    day TEXT NOT NULL,
    power INTEGER NOT NULL DEFAULT 0,
    members_count INTEGER NOT NULL DEFAULT 0,
    members_max INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (game, alliance_name, day)
);
"""


def _ensure_game_scoped_schema(conn: sqlite3.Connection) -> None:
    """One-time migration: rebuild tables with ``game`` in their composite PKs.

    Legacy DBs (pre-Phase 2b) have ``gamers(player_id PK)`` etc. with no game
    column. SQLite can't alter the PK in place, so we recreate each affected
    table and copy rows with ``game = 'wos'`` (the only game that existed).

    No-op on fresh DBs created by ``_SCHEMA_SQL`` above or on already-migrated DBs.
    """

    def _has_game_column(table: str) -> bool:
        return any(row["name"] == "game" for row in conn.execute(f"PRAGMA table_info({table})"))

    # gamers
    if not _has_game_column("gamers"):
        conn.executescript(
            """
            CREATE TABLE gamers_new (
                game TEXT NOT NULL DEFAULT 'wos',
                player_id INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (game, player_id)
            );
            INSERT INTO gamers_new (game, player_id, state_json, updated_at)
                SELECT 'wos', player_id, state_json, updated_at FROM gamers;
            DROP TABLE gamers;
            ALTER TABLE gamers_new RENAME TO gamers;
            """
        )

    # player_power_daily
    if not _has_game_column("player_power_daily"):
        conn.executescript(
            """
            CREATE TABLE player_power_daily_new (
                game TEXT NOT NULL DEFAULT 'wos',
                player_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                power INTEGER NOT NULL,
                furnace_level INTEGER NOT NULL DEFAULT 0,
                gems INTEGER NOT NULL DEFAULT 0,
                arena_rank INTEGER NOT NULL DEFAULT 0,
                arena_power INTEGER NOT NULL DEFAULT 0,
                recorded_at REAL NOT NULL,
                PRIMARY KEY (game, player_id, day)
            );
            INSERT INTO player_power_daily_new
                (game, player_id, day, power, furnace_level, gems,
                 arena_rank, arena_power, recorded_at)
                SELECT 'wos', player_id, day, power, furnace_level,
                       COALESCE(gems, 0), COALESCE(arena_rank, 0),
                       COALESCE(arena_power, 0), recorded_at
                FROM player_power_daily;
            DROP TABLE player_power_daily;
            ALTER TABLE player_power_daily_new RENAME TO player_power_daily;
            """
        )

    # player_level_events — id is auto, so just ADD COLUMN + recreate index
    if not _has_game_column("player_level_events"):
        conn.executescript(
            """
            ALTER TABLE player_level_events
                ADD COLUMN game TEXT NOT NULL DEFAULT 'wos';
            DROP INDEX IF EXISTS idx_level_events_player;
            CREATE INDEX idx_level_events_player
                ON player_level_events (game, player_id, day);
            """
        )

    # alliance_daily
    if not _has_game_column("alliance_daily"):
        conn.executescript(
            """
            CREATE TABLE alliance_daily_new (
                game TEXT NOT NULL DEFAULT 'wos',
                alliance_name TEXT NOT NULL,
                day TEXT NOT NULL,
                power INTEGER NOT NULL DEFAULT 0,
                members_count INTEGER NOT NULL DEFAULT 0,
                members_max INTEGER NOT NULL DEFAULT 0,
                recorded_at REAL NOT NULL,
                PRIMARY KEY (game, alliance_name, day)
            );
            INSERT INTO alliance_daily_new
                (game, alliance_name, day, power, members_count, members_max, recorded_at)
                SELECT 'wos', alliance_name, day, power, members_count,
                       members_max, recorded_at
                FROM alliance_daily;
            DROP TABLE alliance_daily;
            ALTER TABLE alliance_daily_new RENAME TO alliance_daily;
            """
        )

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
    if path is None:
        _maybe_migrate_legacy_db_filename()
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA_SQL)
        _ensure_player_power_daily_columns(conn)
        _ensure_game_scoped_schema(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _default_game() -> str:
    """Local helper — lazy import to avoid circular dependencies."""
    from config.games import default_game

    return default_game()


def load_state_db_raw(game: str | None = None) -> tuple[StateDB, str | None, str]:
    """Load StateDB from SQLite, scoped to ``game`` (default: the platform default).

    Returns ``(db, parse_error, raw_json_for_debug)``. Pass ``game="*"`` to load
    every game's gamers in one batch (rare — dashboard cross-game views).
    """
    g = (game or _default_game()).strip()
    try:
        with _conn_lock, _connect() as conn:
            if g == "*":
                rows = conn.execute(
                    "SELECT game, player_id, state_json FROM gamers "
                    "ORDER BY game, player_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT game, player_id, state_json FROM gamers "
                    "WHERE game = ? ORDER BY player_id",
                    (g,),
                ).fetchall()
        gamers: list[GamerState] = []
        for row in rows:
            raw = json.loads(row["state_json"])
            raw.setdefault("game", row["game"])
            gamers.append(GamerState.model_validate(raw))
        db = StateDB(gamers=gamers)
        return db, None, json.dumps(db.model_dump(mode="json"), indent=2)
    except Exception as exc:
        return StateDB(), f"{type(exc).__name__}: {exc}", ""


def save_state_db(db: StateDB, game: str | None = None) -> None:
    """Persist every gamer in ``db``. Writes are scoped per-game so other
    games' rows are preserved.

    If ``game`` is given, it overrides each gamer's ``game`` field (handy for
    tests and for collapsing legacy single-game flows). Otherwise every gamer
    keeps the game stored in its model (defaulting to the platform default
    when absent).
    """
    now = time.time()
    default = _default_game()
    rows: list[tuple[str, int, str, float]] = []
    games_touched: set[str] = set()
    for g in db.gamers:
        g_game = (game or getattr(g, "game", None) or default).strip()
        rows.append(
            (
                g_game,
                int(g.id),
                json.dumps(g.model_dump(mode="json"), separators=(",", ":")),
                now,
            )
        )
        games_touched.add(g_game)
    with _conn_lock, _connect() as conn:
        # Delete only the games we're about to rewrite — leaves other games' rows alone.
        for g_game in games_touched:
            conn.execute("DELETE FROM gamers WHERE game = ?", (g_game,))
        if rows:
            conn.executemany(
                "INSERT INTO gamers (game, player_id, state_json, updated_at) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
        conn.commit()


def delete_player_state(player_id: str | int, game: str | None = None) -> dict[str, int]:
    """Wipe all persisted rows for one ``(game, player_id)`` across gamers + stats tables."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError) as exc:
        msg = f"invalid player_id: {player_id!r}"
        raise ValueError(msg) from exc
    g = (game or _default_game()).strip()
    counts: dict[str, int] = {}
    with _conn_lock, _connect() as conn:
        for table in ("gamers", "player_power_daily", "player_level_events"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE game = ? AND player_id = ?",
                (g, pid),
            )
            counts[table] = int(cur.rowcount or 0)
        conn.commit()
    return counts


def record_player_stats(gamer: GamerState, game: str | None = None) -> None:
    """Upsert today's snapshot; record furnace level-up events; mirror alliance row."""
    g = (game or getattr(gamer, "game", None) or _default_game()).strip()
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
                (game, player_id, day, power, furnace_level, gems,
                 arena_rank, arena_power, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game, player_id, day) DO UPDATE SET
                power = excluded.power,
                furnace_level = excluded.furnace_level,
                gems = excluded.gems,
                arena_rank = excluded.arena_rank,
                arena_power = excluded.arena_power,
                recorded_at = excluded.recorded_at
            """,
            (g, pid, day, power, furnace_level, gems, arena_rank, arena_power, now),
        )
        prev = conn.execute(
            """
            SELECT MAX(level) AS max_level FROM player_level_events
            WHERE game = ? AND player_id = ?
            """,
            (g, pid),
        ).fetchone()
        prev_level = int(prev["max_level"] or 0) if prev else 0
        if furnace_level > prev_level:
            conn.execute(
                """
                INSERT INTO player_level_events
                    (game, player_id, level, day, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (g, pid, furnace_level, day, now),
            )
        if alliance_name:
            conn.execute(
                """
                INSERT INTO alliance_daily
                    (game, alliance_name, day, power, members_count, members_max, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game, alliance_name, day) DO UPDATE SET
                    power = excluded.power,
                    members_count = excluded.members_count,
                    members_max = excluded.members_max,
                    recorded_at = excluded.recorded_at
                """,
                (g, alliance_name, day, alliance_power, members_count, members_max, now),
            )
        conn.commit()


def get_player_stats(player_id: str, game: str | None = None) -> dict[str, Any]:
    pid = int(str(player_id).strip())
    g = (game or _default_game()).strip()
    with _conn_lock, _connect() as conn:
        gamer_row = conn.execute(
            "SELECT state_json FROM gamers WHERE game = ? AND player_id = ?",
            (g, pid),
        ).fetchone()
        series_rows = conn.execute(
            """
            SELECT day, power, furnace_level, gems, arena_rank, arena_power
            FROM player_power_daily
            WHERE game = ? AND player_id = ?
            ORDER BY day ASC
            """,
            (g, pid),
        ).fetchall()
        level_rows = conn.execute(
            """
            SELECT day, level
            FROM player_level_events
            WHERE game = ? AND player_id = ?
            ORDER BY day ASC, level ASC
            """,
            (g, pid),
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
        "game": g,
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


def list_alliance_names(game: str | None = None) -> list[str]:
    g = (game or _default_game()).strip()
    with _conn_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT alliance_name FROM alliance_daily "
            "WHERE game = ? ORDER BY alliance_name ASC",
            (g,),
        ).fetchall()
    return [r["alliance_name"] for r in rows if r["alliance_name"]]


def get_alliance_stats(alliance_name: str, game: str | None = None) -> dict[str, Any]:
    name = str(alliance_name).strip()
    g = (game or _default_game()).strip()
    with _conn_lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT day, power, members_count, members_max
            FROM alliance_daily
            WHERE game = ? AND alliance_name = ?
            ORDER BY day ASC
            """,
            (g, name),
        ).fetchall()
    return {
        "alliance_name": name,
        "game": g,
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


