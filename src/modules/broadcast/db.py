"""SQLite catalog + send-log for alliance broadcasts.

The catalog is the single source of truth (no YAML) — created and edited from the
dashboard. Lives in the shared ``state.db`` alongside devices/gamers/calendar,
wired through :mod:`config.orm` exactly like :mod:`games.wos.core.calendar.db`.

Rows round-trip to the pure :class:`~.models.BroadcastMessage` so the selection
engine never touches the database.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from sqlmodel import Field, Session, SQLModel, delete, select

from config import orm
from config.state_sqlite import state_db_path

from .models import CHANNEL_ALLIANCE, BroadcastMessage

if TYPE_CHECKING:
    import sqlite3

    from sqlalchemy.engine import Engine

_lock = threading.RLock()


class BroadcastMessageRow(SQLModel, table=True):
    __tablename__ = "broadcast_messages"

    id: str = Field(primary_key=True)
    title: str = ""
    text: str = ""
    category: str = "custom"
    game_scope: str = "all"
    channel: str = CHANNEL_ALLIANCE
    trigger_kind: str = "cron"
    cron: str = ""
    cond: str = ""
    lead_hours: int = 0
    cooldown_minutes: int = 360
    priority: int = 100
    quiet_start_hour: int = -1
    quiet_end_hour: int = -1
    target_alliance: str = ""
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0


class BroadcastSendRow(SQLModel, table=True):
    __tablename__ = "broadcast_sends"

    id: int | None = Field(default=None, primary_key=True)
    message_id: str = Field(index=True)
    game: str = ""
    alliance: str = ""
    fid: str = ""
    text: str = ""
    sent_at: float = 0.0


def _broadcast_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(broadcast_messages)")}


def _add_channel_column(conn: sqlite3.Connection) -> None:
    """Add the ``channel`` column to a pre-existing catalog table (defensive)."""
    if "channel" not in _broadcast_columns(conn):
        conn.execute(
            "ALTER TABLE broadcast_messages "
            f"ADD COLUMN channel TEXT NOT NULL DEFAULT '{CHANNEL_ALLIANCE}'"
        )


def _add_round2_columns(conn: sqlite3.Connection) -> None:
    """Add pre-event / quiet-hours / targeting columns (defensive ADD COLUMN)."""
    cols = _broadcast_columns(conn)
    adds = (
        ("lead_hours", "INTEGER NOT NULL DEFAULT 0"),
        ("quiet_start_hour", "INTEGER NOT NULL DEFAULT -1"),
        ("quiet_end_hour", "INTEGER NOT NULL DEFAULT -1"),
        ("target_alliance", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, decl in adds:
        if name not in cols:
            conn.execute(f"ALTER TABLE broadcast_messages ADD COLUMN {name} {decl}")


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(
        engine, tables=[BroadcastMessageRow.__table__, BroadcastSendRow.__table__]
    )
    # ``create_all`` is CREATE-IF-NOT-EXISTS, so it won't add columns to a table
    # created before they existed — migrate explicitly, once per DB.
    orm.apply_migrations(
        engine,
        "broadcast",
        [("001_channel", _add_channel_column), ("002_round2", _add_round2_columns)],
    )


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "broadcast", _ensure_schema)
    return engine


def _to_message(row: BroadcastMessageRow) -> BroadcastMessage:
    return BroadcastMessage(
        id=row.id,
        title=row.title,
        text=row.text,
        category=row.category,
        game_scope=row.game_scope,
        channel=row.channel or CHANNEL_ALLIANCE,
        trigger_kind=row.trigger_kind,
        cron=row.cron or "",
        cond=row.cond or "",
        lead_hours=int(row.lead_hours or 0),
        cooldown_minutes=int(row.cooldown_minutes),
        priority=int(row.priority),
        quiet_start_hour=int(row.quiet_start_hour if row.quiet_start_hour is not None else -1),
        quiet_end_hour=int(row.quiet_end_hour if row.quiet_end_hour is not None else -1),
        target_alliance=row.target_alliance or "",
        enabled=bool(row.enabled),
        created_at=float(row.created_at or 0.0),
        updated_at=float(row.updated_at or 0.0),
    )


def list_messages(*, game: str | None = None, enabled_only: bool = False) -> list[BroadcastMessage]:
    """All catalog messages, newest-first; optionally filtered to a game/enabled.

    ``game`` filtering includes ``all``-scoped messages (they apply everywhere).
    """
    with _lock, Session(_engine()) as session:
        rows = list(session.exec(select(BroadcastMessageRow)).all())
    out = [_to_message(r) for r in rows]
    if enabled_only:
        out = [m for m in out if m.enabled]
    if game:
        out = [m for m in out if m.applies_to_game(game)]
    out.sort(key=lambda m: (-m.updated_at, m.id))
    return out


def get_message(message_id: str) -> BroadcastMessage | None:
    with _lock, Session(_engine()) as session:
        row = session.get(BroadcastMessageRow, str(message_id))
        return _to_message(row) if row else None


def upsert_message(msg: BroadcastMessage, *, now: float | None = None) -> BroadcastMessage:
    """Insert or replace a message by id; stamps ``created_at``/``updated_at``."""
    ts = time.time() if now is None else now
    with _lock, Session(_engine()) as session:
        row = session.get(BroadcastMessageRow, msg.id)
        created = row.created_at if row and row.created_at else ts
        if row is None:
            row = BroadcastMessageRow(id=msg.id)
        row.title = msg.title
        row.text = msg.text
        row.category = msg.category
        row.game_scope = msg.game_scope
        row.channel = msg.channel
        row.trigger_kind = msg.trigger_kind
        row.cron = msg.cron or ""
        row.cond = msg.cond or ""
        row.lead_hours = int(msg.lead_hours)
        row.cooldown_minutes = int(msg.cooldown_minutes)
        row.priority = int(msg.priority)
        row.quiet_start_hour = int(msg.quiet_start_hour)
        row.quiet_end_hour = int(msg.quiet_end_hour)
        row.target_alliance = msg.target_alliance or ""
        row.enabled = bool(msg.enabled)
        row.created_at = created
        row.updated_at = ts
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_message(row)


def set_enabled(message_id: str, enabled: bool, *, now: float | None = None) -> BroadcastMessage | None:
    ts = time.time() if now is None else now
    with _lock, Session(_engine()) as session:
        row = session.get(BroadcastMessageRow, str(message_id))
        if row is None:
            return None
        row.enabled = bool(enabled)
        row.updated_at = ts
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_message(row)


def delete_message(message_id: str) -> bool:
    with _lock, Session(_engine()) as session:
        row = session.get(BroadcastMessageRow, str(message_id))
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def record_send(
    *, message_id: str, game: str, alliance: str, fid: str, text: str, sent_at: float | None = None
) -> None:
    """Append one successful post to the send-log (dashboard history)."""
    ts = time.time() if sent_at is None else sent_at
    with _lock, Session(_engine()) as session:
        session.add(
            BroadcastSendRow(
                message_id=str(message_id),
                game=str(game),
                alliance=str(alliance),
                fid=str(fid),
                text=str(text),
                sent_at=ts,
            )
        )
        session.commit()


def recent_sends(
    *, game: str | None = None, alliance: str | None = None, limit: int = 50
) -> list[BroadcastSendRow]:
    """Recent posts, newest-first; optionally filtered by game/alliance."""
    with _lock, Session(_engine()) as session:
        stmt = select(BroadcastSendRow)
        if game:
            stmt = stmt.where(BroadcastSendRow.game == game)
        if alliance:
            stmt = stmt.where(BroadcastSendRow.alliance == alliance)
        stmt = stmt.order_by(BroadcastSendRow.sent_at.desc()).limit(max(1, int(limit)))
        return list(session.exec(stmt).all())


def clear_all_for_tests() -> None:
    """Wipe both tables — test helper only."""
    with _lock, Session(_engine()) as session:
        session.exec(delete(BroadcastSendRow))
        session.exec(delete(BroadcastMessageRow))
        session.commit()
