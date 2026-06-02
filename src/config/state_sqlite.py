"""SQLModel persistence for per-gamer state and daily power / level statistics."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Index, column, delete, func
from sqlmodel import Field, Session, SQLModel, select

from config import orm
from config.paths import repo_root
from config.state_schema import GamerState, StateDB

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from sqlalchemy.engine import Engine

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
    orm.reset_for_tests()


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class GamerRow(SQLModel, table=True):
    __tablename__ = "gamers"

    game: str = Field(default="wos", primary_key=True)
    player_id: int = Field(primary_key=True)
    state_json: str
    updated_at: float


class PlayerPowerDaily(SQLModel, table=True):
    __tablename__ = "player_power_daily"

    game: str = Field(default="wos", primary_key=True)
    player_id: int = Field(primary_key=True)
    day: str = Field(primary_key=True)
    power: int
    furnace_level: int = Field(default=0)
    gems: int = Field(default=0)
    arena_rank: int = Field(default=0)
    arena_power: int = Field(default=0)
    recorded_at: float


class PlayerLevelEvent(SQLModel, table=True):
    __tablename__ = "player_level_events"
    __table_args__ = (
        Index("idx_level_events_player", "game", "player_id", "day"),
    )

    id: int | None = Field(default=None, primary_key=True)
    game: str = Field(default="wos")
    player_id: int
    level: int
    day: str
    recorded_at: float


class AllianceDaily(SQLModel, table=True):
    __tablename__ = "alliance_daily"

    game: str = Field(default="wos", primary_key=True)
    alliance_name: str = Field(primary_key=True)
    day: str = Field(primary_key=True)
    power: int = Field(default=0)
    members_count: int = Field(default=0)
    members_max: int = Field(default=0)
    recorded_at: float


# ---------------------------------------------------------------------------
# schema setup + legacy migrations
# ---------------------------------------------------------------------------


def _ensure_game_scoped_schema(conn: sqlite3.Connection) -> None:
    """One-time migration: rebuild tables with ``game`` in their composite PKs.

    Legacy DBs (pre-Phase 2b) have ``gamers(player_id PK)`` etc. with no game
    column. SQLite can't alter the PK in place, so we recreate each affected
    table and copy rows with ``game = 'wos'`` (the only game that existed).

    No-op on fresh DBs created by ``create_all`` above or on already-migrated DBs.
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
    if not existing:
        return  # table absent — create_all built it with all columns
    for col in _PLAYER_POWER_DAILY_COLS:
        if col not in existing:
            conn.execute(
                f"ALTER TABLE player_power_daily ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
            )


def _ensure_gamers_power_index(conn: sqlite3.Connection) -> None:
    """Expose ``state_json.$.power`` as an indexed VIRTUAL generated column.

    ``gamers.state_json`` is an opaque blob; a query like "all gamers with power
    > X" would otherwise have to load and parse every row. A VIRTUAL generated
    column costs nothing at rest and, with the index below, lets such filters be
    served from the index. It's deliberately *not* on the ``GamerRow`` model —
    inserts/reads never touch it — so the write path is unchanged.
    """
    # table_xinfo (not table_info) lists generated columns, so this stays correct
    # and idempotent even if the migration record is ever lost while the column exists.
    existing = {row["name"] for row in conn.execute("PRAGMA table_xinfo(gamers)")}
    if not existing:
        return  # table absent — should not happen (create_all ran first)
    if "power" not in existing:
        conn.execute(
            "ALTER TABLE gamers ADD COLUMN power INTEGER "
            "GENERATED ALWAYS AS (json_extract(state_json, '$.power')) VIRTUAL"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gamers_power ON gamers(game, power)")


def _ensure_schema(engine: Engine) -> None:
    """Create missing tables, then run the tracked legacy game-scoping migrations.

    ``create_all`` only builds *missing* tables, so legacy single-game DBs keep
    their old (gameless) PK shape — the migrations below rebuild those. Order
    matters: add the ``player_power_daily`` value columns *before* the rebuild
    copies them.
    """
    SQLModel.metadata.create_all(
        engine,
        tables=[
            GamerRow.__table__,
            PlayerPowerDaily.__table__,
            PlayerLevelEvent.__table__,
            AllianceDaily.__table__,
        ],
    )
    orm.apply_migrations(engine, "state", [
        ("001_add_power_columns", _ensure_player_power_daily_columns),
        ("002_game_scoped_rebuild", _ensure_game_scoped_schema),
        ("003_gamers_power_index", _ensure_gamers_power_index),
    ])


def _engine() -> Engine:
    if _path_override is None:
        _maybe_migrate_legacy_db_filename()
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "state", _ensure_schema)
    return engine


def _today_iso() -> str:
    return datetime.now(tz=UTC).date().isoformat()


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
        with _conn_lock, Session(_engine()) as s:
            stmt = select(GamerRow)
            if g == "*":
                stmt = stmt.order_by(GamerRow.game, GamerRow.player_id)
            else:
                stmt = stmt.where(GamerRow.game == g).order_by(GamerRow.player_id)
            rows = s.exec(stmt).all()
        gamers: list[GamerState] = []
        for row in rows:
            raw = json.loads(row.state_json)
            raw.setdefault("game", row.game)
            gamers.append(GamerState.model_validate(raw))
        db = StateDB(gamers=gamers)
        return db, None, json.dumps(db.model_dump(mode="json"), indent=2)
    except Exception as exc:
        return StateDB(), f"{type(exc).__name__}: {exc}", ""


def list_gamers_by_power(min_power: int, game: str | None = None) -> list[GamerState]:
    """Return gamers with ``power >= min_power``, served from ``idx_gamers_power``.

    Filters on the ``power`` VIRTUAL generated column (``state_json.$.power``) so
    the database does the selection — no need to load and parse every blob.
    """
    g = (game or _default_game()).strip()
    with _conn_lock, Session(_engine()) as s:
        rows = s.exec(
            select(GamerRow)
            .where(GamerRow.game == g, column("power") >= min_power)
            .order_by(column("power").desc())
        ).all()
    gamers: list[GamerState] = []
    for row in rows:
        raw = json.loads(row.state_json)
        raw.setdefault("game", row.game)
        gamers.append(GamerState.model_validate(raw))
    return gamers


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
    new_rows: list[GamerRow] = []
    games_touched: set[str] = set()
    for g in db.gamers:
        g_game = (game or getattr(g, "game", None) or default).strip()
        new_rows.append(
            GamerRow(
                game=g_game,
                player_id=int(g.id),
                state_json=json.dumps(g.model_dump(mode="json"), separators=(",", ":")),
                updated_at=now,
            )
        )
        games_touched.add(g_game)
    with _conn_lock, Session(_engine()) as s:
        # Delete only the games we're about to rewrite — leaves other games' rows
        # alone. One bulk DELETE per game; it runs immediately, so the reused PKs
        # are gone before the INSERTs below.
        for g_game in games_touched:
            s.execute(delete(GamerRow).where(GamerRow.game == g_game))
        s.add_all(new_rows)
        s.commit()


def delete_player_state(player_id: str | int, game: str | None = None) -> dict[str, int]:
    """Wipe all persisted rows for one ``(game, player_id)`` across gamers + stats tables."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError) as exc:
        msg = f"invalid player_id: {player_id!r}"
        raise ValueError(msg) from exc
    g = (game or _default_game()).strip()
    counts: dict[str, int] = {}
    with _conn_lock, Session(_engine()) as s:
        for model in (GamerRow, PlayerPowerDaily, PlayerLevelEvent):
            result = s.execute(
                delete(model).where(model.game == g, model.player_id == pid)
            )
            counts[model.__tablename__] = int(result.rowcount or 0)
        s.commit()
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

    with _conn_lock, Session(_engine()) as s:
        ppd = s.get(PlayerPowerDaily, (g, pid, day))
        if ppd is None:
            s.add(PlayerPowerDaily(
                game=g, player_id=pid, day=day, power=power, furnace_level=furnace_level,
                gems=gems, arena_rank=arena_rank, arena_power=arena_power, recorded_at=now,
            ))
        else:
            ppd.power = power
            ppd.furnace_level = furnace_level
            ppd.gems = gems
            ppd.arena_rank = arena_rank
            ppd.arena_power = arena_power
            ppd.recorded_at = now
            s.add(ppd)

        prev_level = int(s.scalar(
            select(func.max(PlayerLevelEvent.level))
            .where(PlayerLevelEvent.game == g, PlayerLevelEvent.player_id == pid)
        ) or 0)
        if furnace_level > prev_level:
            s.add(PlayerLevelEvent(
                game=g, player_id=pid, level=furnace_level, day=day, recorded_at=now,
            ))

        if alliance_name:
            ad = s.get(AllianceDaily, (g, alliance_name, day))
            if ad is None:
                s.add(AllianceDaily(
                    game=g, alliance_name=alliance_name, day=day, power=alliance_power,
                    members_count=members_count, members_max=members_max, recorded_at=now,
                ))
            else:
                ad.power = alliance_power
                ad.members_count = members_count
                ad.members_max = members_max
                ad.recorded_at = now
                s.add(ad)
        s.commit()


def get_player_stats(player_id: str, game: str | None = None) -> dict[str, Any]:
    pid = int(str(player_id).strip())
    g = (game or _default_game()).strip()
    with _conn_lock, Session(_engine()) as s:
        gamer_row = s.get(GamerRow, (g, pid))
        series_rows = s.exec(
            select(PlayerPowerDaily)
            .where(PlayerPowerDaily.game == g, PlayerPowerDaily.player_id == pid)
            .order_by(PlayerPowerDaily.day.asc())
        ).all()
        level_rows = s.exec(
            select(PlayerLevelEvent)
            .where(PlayerLevelEvent.game == g, PlayerLevelEvent.player_id == pid)
            .order_by(PlayerLevelEvent.day.asc(), PlayerLevelEvent.level.asc())
        ).all()

    nickname = ""
    if gamer_row:
        try:
            gamer = GamerState.model_validate(json.loads(gamer_row.state_json))
            nickname = gamer.nickname or ""
        except Exception:
            logger.debug("get_player_stats: invalid gamer json pid=%s", pid, exc_info=True)

    return {
        "player_id": str(pid),
        "game": g,
        "nickname": nickname,
        "series": [
            {
                "day": r.day,
                "power": int(r.power),
                "furnace_level": int(r.furnace_level),
                "gems": int(r.gems or 0),
                "arena_rank": int(r.arena_rank or 0),
                "arena_power": int(r.arena_power or 0),
            }
            for r in series_rows
        ],
        "level_events": [
            {"day": r.day, "level": int(r.level)}
            for r in level_rows
        ],
    }


def list_alliance_names(game: str | None = None) -> list[str]:
    g = (game or _default_game()).strip()
    with _conn_lock, Session(_engine()) as s:
        rows = s.exec(
            select(AllianceDaily.alliance_name)
            .where(AllianceDaily.game == g)
            .distinct()
            .order_by(AllianceDaily.alliance_name.asc())
        ).all()
    return [name for name in rows if name]


def get_alliance_stats(alliance_name: str, game: str | None = None) -> dict[str, Any]:
    name = str(alliance_name).strip()
    g = (game or _default_game()).strip()
    with _conn_lock, Session(_engine()) as s:
        rows = s.exec(
            select(AllianceDaily)
            .where(AllianceDaily.game == g, AllianceDaily.alliance_name == name)
            .order_by(AllianceDaily.day.asc())
        ).all()
    return {
        "alliance_name": name,
        "game": g,
        "series": [
            {
                "day": r.day,
                "power": int(r.power or 0),
                "members_count": int(r.members_count or 0),
                "members_max": int(r.members_max or 0),
            }
            for r in rows
        ],
    }
