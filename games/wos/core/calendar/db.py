"""Durable per-state event schedule (one row per event occurrence).

Events are state-wide, so the schedule is keyed by ``(game, state)`` — read once
by one bot, persisted here, queried by everyone on that state. Lives in the
shared encrypted ``state.db`` (SQLCipher) alongside devices/gamers/giftcodes,
wired through :mod:`config.orm` exactly like :mod:`config.giftcodes_db`.

A calendar read yields the *current* full window for a state, so
:func:`replace_state_schedule` swaps the whole set atomically (delete + insert)
rather than upserting row-by-row — that drops events that rolled off the
calendar instead of leaving them stale. ``starts_at`` is part of the key so a
recurring event's next occurrence is a distinct row.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from sqlmodel import Field, Session, SQLModel, delete, select

from config import orm
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_DEFAULT_GAME = "wos"
_lock = threading.RLock()


class CalendarEventRow(SQLModel, table=True):
    __tablename__ = "calendar_events"

    game: str = Field(default=_DEFAULT_GAME, primary_key=True)
    state: str = Field(primary_key=True)        # in-game state/server number
    name: str = Field(primary_key=True)         # event name, OCR'd from the popup
    starts_at: str = Field(primary_key=True)    # ISO-8601 UTC
    ends_at: str
    source: str = Field(default="popup_ocr")
    updated_at: float


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=[CalendarEventRow.__table__])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "calendar", _ensure_schema)
    return engine


def replace_state_schedule(
    state: str,
    events: list[tuple[str, datetime, datetime]],
    *,
    game: str = _DEFAULT_GAME,
    source: str = "popup_ocr",
    now: float | None = None,
) -> int:
    """Atomically replace a state's schedule with a freshly-read set.

    ``events`` is ``[(name, starts_at, ends_at), ...]``. Returns the row count
    written. A no-op-safe empty list clears the state's schedule.
    """
    if not state:
        return 0
    ts = time.time() if now is None else now
    rows = [
        CalendarEventRow(
            game=game,
            state=state,
            name=name.strip(),
            starts_at=start.isoformat(),
            ends_at=end.isoformat(),
            source=source,
            updated_at=ts,
        )
        for name, start, end in events
        if name and name.strip()
    ]
    # De-dup on the composite key (same event tapped twice across scroll frames).
    by_key = {(r.game, r.state, r.name, r.starts_at): r for r in rows}
    with _lock, Session(_engine()) as session:
        session.exec(
            delete(CalendarEventRow).where(
                CalendarEventRow.game == game,
                CalendarEventRow.state == state,
            )
        )
        session.add_all(list(by_key.values()))
        session.commit()
    return len(by_key)


def get_state_schedule(state: str, *, game: str = _DEFAULT_GAME) -> list[CalendarEventRow]:
    """All stored events for a state, ordered by start time."""
    if not state:
        return []
    with _lock, Session(_engine()) as session:
        stmt = (
            select(CalendarEventRow)
            .where(CalendarEventRow.game == game, CalendarEventRow.state == state)
            .order_by(CalendarEventRow.starts_at)
        )
        return list(session.exec(stmt).all())


def list_states(*, game: str = _DEFAULT_GAME) -> list[str]:
    """Distinct states that have a stored schedule, sorted."""
    with _lock, Session(_engine()) as session:
        stmt = (
            select(CalendarEventRow.state)
            .where(CalendarEventRow.game == game)
            .distinct()
            .order_by(CalendarEventRow.state)
        )
        return [str(s) for s in session.exec(stmt).all()]
