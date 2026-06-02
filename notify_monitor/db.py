"""SQLModel data layer.

Persistence is defined with `SQLModel` table models (SQLAlchemy + Pydantic).
A short-lived `Session` is opened per operation (WAL mode, cheap on SQLite) so
the background polling thread and the FastAPI request threads never share a
session. Writes are serialized through a module-level lock for safety.

The engine is resolved from ``config.DB_PATH`` on first use and cached per path,
so tests that monkeypatch ``config.DB_PATH`` get an isolated database.

Public functions return plain ``dict``s (via ``model_dump()``) to keep the
JSON/API contract identical to the previous raw-sqlite layer.

Tables
------
players                    id, nickname, game, active, created_at   UNIQUE(nickname, game)
patterns                   id, game, pattern_regex, event_type, description, active
events                     id, game, player, event_type, raw_text, timestamp
unrecognized_notifications id, game, raw_text, timestamp, reviewed
settings                   key, value      (runtime-tunable config)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import event, func
from sqlmodel import Field, Session, SQLModel, UniqueConstraint, col, create_engine, select

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from . import config
from .logging_setup import get_logger

log = get_logger("db")
_write_lock = threading.Lock()
_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


# --- models ----------------------------------------------------------------

class Player(SQLModel, table=True):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("nickname", "game", name="uq_players_nickname_game"),)

    id: int | None = Field(default=None, primary_key=True)
    nickname: str
    game: str
    active: bool = True
    created_at: str


class Pattern(SQLModel, table=True):
    __tablename__ = "patterns"

    id: int | None = Field(default=None, primary_key=True)
    game: str
    pattern_regex: str
    event_type: str
    description: str = ""
    active: bool = True


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    game: str
    player: str
    event_type: str
    raw_text: str
    timestamp: str = Field(index=True)


class UnrecognizedNotification(SQLModel, table=True):
    __tablename__ = "unrecognized_notifications"

    id: int | None = Field(default=None, primary_key=True)
    game: str
    raw_text: str
    timestamp: str
    reviewed: bool = Field(default=False, index=True)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str


DEFAULT_SETTINGS = {
    "poll_interval": str(config.DEFAULT_POLL_INTERVAL),
    "adb_serial": config.DEFAULT_ADB_SERIAL,
    "adb_path": config.DEFAULT_ADB_PATH,
    "monitor_enabled": "1",
}


# --- engine ----------------------------------------------------------------

def _make_engine(path: str) -> Engine:
    """Create a WAL-mode SQLite engine that is safe to share across threads."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001 - sqlalchemy hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # Block-and-retry instead of erroring when the poller thread and a web
        # request write at the same time.
        cur.execute("PRAGMA busy_timeout=5000")
        # Durable under WAL; faster writes than the default FULL.
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA wal_autocheckpoint=1000")
        cur.close()

    return engine


def _engine() -> Engine:
    """Return the engine for the current ``config.DB_PATH`` (cached per path)."""
    key = str(config.DB_PATH)
    with _engines_lock:
        engine = _engines.get(key)
        if engine is None:
            engine = _make_engine(key)
            _engines[key] = engine
        return engine


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def init_db() -> None:
    """Run schema migrations and seed defaults (see ``migrations.py``)."""
    from . import migrations  # local import to avoid a circular import at module load

    with _write_lock:
        migrations.run_migrations(_engine())
    log.info("Database ready at %s", config.DB_PATH)


# --- settings --------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    with Session(_engine()) as s:
        row = s.get(Setting, key)
        return row.value if row else default


def get_all_settings() -> dict[str, str]:
    with Session(_engine()) as s:
        return {r.key: r.value for r in s.exec(select(Setting)).all()}


def set_setting(key: str, value: str) -> None:
    with _write_lock, Session(_engine()) as s:
        row = s.get(Setting, key)
        if row:
            row.value = str(value)
            s.add(row)
        else:
            s.add(Setting(key=key, value=str(value)))
        s.commit()


# --- players ---------------------------------------------------------------

def list_players(game: str | None = None) -> list[dict[str, Any]]:
    with Session(_engine()) as s:
        stmt = select(Player)
        if game:
            stmt = stmt.where(Player.game == game)
        stmt = stmt.order_by(Player.game, Player.nickname)
        return [p.model_dump() for p in s.exec(stmt).all()]


def add_player(nickname: str, game: str, active: bool = True) -> int:
    nickname = nickname.strip()
    with _write_lock, Session(_engine()) as s:
        row = s.exec(
            select(Player).where(Player.nickname == nickname, Player.game == game)
        ).first()
        if row:
            row.active = bool(active)
            s.add(row)
            s.commit()
            return int(row.id)
        player = Player(nickname=nickname, game=game, active=bool(active), created_at=_now())
        s.add(player)
        s.commit()
        s.refresh(player)
        return int(player.id)


def ensure_player(nickname: str, game: str) -> dict[str, Any] | None:
    """Auto-discover a player. Returns the row, or None for blank nicknames.

    New players are created active=1. Existing rows (incl. deactivated ones)
    are returned untouched so operator toggles are respected.
    """
    nickname = (nickname or "").strip()
    if not nickname:
        return None
    with Session(_engine()) as s:
        row = s.exec(
            select(Player).where(Player.nickname == nickname, Player.game == game)
        ).first()
        if row:
            return row.model_dump()
    add_player(nickname, game, active=True)
    log.info("Auto-discovered player '%s' (%s)", nickname, game)
    with Session(_engine()) as s:
        row = s.exec(
            select(Player).where(Player.nickname == nickname, Player.game == game)
        ).first()
        return row.model_dump() if row else None


def set_player_active(player_id: int, active: bool) -> None:
    with _write_lock, Session(_engine()) as s:
        row = s.get(Player, player_id)
        if row:
            row.active = bool(active)
            s.add(row)
            s.commit()


def delete_player(player_id: int) -> None:
    with _write_lock, Session(_engine()) as s:
        row = s.get(Player, player_id)
        if row:
            s.delete(row)
            s.commit()


# --- patterns --------------------------------------------------------------

def list_patterns(game: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    with Session(_engine()) as s:
        stmt = select(Pattern)
        if game:
            stmt = stmt.where(Pattern.game == game)
        if active_only:
            stmt = stmt.where(col(Pattern.active).is_(True))
        stmt = stmt.order_by(Pattern.game, Pattern.event_type)
        return [p.model_dump() for p in s.exec(stmt).all()]


def add_pattern(game: str, pattern_regex: str, event_type: str, description: str = "", active: bool = True) -> int:
    with _write_lock, Session(_engine()) as s:
        pattern = Pattern(
            game=game, pattern_regex=pattern_regex, event_type=event_type,
            description=description, active=bool(active),
        )
        s.add(pattern)
        s.commit()
        s.refresh(pattern)
        return int(pattern.id)


def update_pattern(pattern_id: int, **fields: Any) -> None:
    allowed = {"game", "pattern_regex", "event_type", "description", "active"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    if "active" in sets:
        sets["active"] = bool(sets["active"])
    with _write_lock, Session(_engine()) as s:
        row = s.get(Pattern, pattern_id)
        if not row:
            return
        for key, value in sets.items():
            setattr(row, key, value)
        s.add(row)
        s.commit()


def delete_pattern(pattern_id: int) -> None:
    with _write_lock, Session(_engine()) as s:
        row = s.get(Pattern, pattern_id)
        if row:
            s.delete(row)
            s.commit()


# --- events ----------------------------------------------------------------

def add_event(game: str, player: str, event_type: str, raw_text: str, timestamp: str | None = None) -> int:
    with _write_lock, Session(_engine()) as s:
        evt = Event(
            game=game, player=player, event_type=event_type,
            raw_text=raw_text, timestamp=timestamp or _now(),
        )
        s.add(evt)
        s.commit()
        s.refresh(evt)
        return int(evt.id)


def list_events(limit: int = 100, game: str | None = None, player: str | None = None) -> list[dict[str, Any]]:
    with Session(_engine()) as s:
        stmt = select(Event)
        if game:
            stmt = stmt.where(Event.game == game)
        if player:
            stmt = stmt.where(Event.player == player)
        stmt = stmt.order_by(col(Event.id).desc()).limit(limit)
        return [e.model_dump() for e in s.exec(stmt).all()]


# --- unrecognized ----------------------------------------------------------

def add_unrecognized(game: str, raw_text: str, timestamp: str | None = None) -> int:
    with _write_lock, Session(_engine()) as s:
        notif = UnrecognizedNotification(
            game=game, raw_text=raw_text, timestamp=timestamp or _now(), reviewed=False,
        )
        s.add(notif)
        s.commit()
        s.refresh(notif)
        return int(notif.id)


def list_unrecognized(limit: int = 200, include_reviewed: bool = False) -> list[dict[str, Any]]:
    with Session(_engine()) as s:
        stmt = select(UnrecognizedNotification)
        if not include_reviewed:
            stmt = stmt.where(col(UnrecognizedNotification.reviewed).is_(False))
        stmt = stmt.order_by(col(UnrecognizedNotification.id).desc()).limit(limit)
        return [u.model_dump() for u in s.exec(stmt).all()]


def set_unrecognized_reviewed(notif_id: int, reviewed: bool = True) -> None:
    with _write_lock, Session(_engine()) as s:
        row = s.get(UnrecognizedNotification, notif_id)
        if row:
            row.reviewed = bool(reviewed)
            s.add(row)
            s.commit()


def get_unrecognized(notif_id: int) -> dict[str, Any] | None:
    with Session(_engine()) as s:
        row = s.get(UnrecognizedNotification, notif_id)
        return row.model_dump() if row else None


def counts() -> dict[str, int]:
    """Summary counts used by the dashboard."""
    with Session(_engine()) as s:
        def count(stmt: Any) -> int:
            return int(s.scalar(stmt))
        return {
            "players": count(select(func.count()).select_from(Player)),
            "active_players": count(
                select(func.count()).select_from(Player).where(col(Player.active).is_(True))
            ),
            "patterns": count(select(func.count()).select_from(Pattern)),
            "events": count(select(func.count()).select_from(Event)),
            "unrecognized": count(
                select(func.count()).select_from(UnrecognizedNotification)
                .where(col(UnrecognizedNotification.reviewed).is_(False))
            ),
        }
