"""Durable per-alliance Bear Hunt trap cooldowns (one row per trap).

Bear Hunt is a single alliance-wide event: both traps share one cooldown clock
for the whole alliance, so the schedule is keyed by ``(game, alliance_name)`` —
read once by any member, persisted here, queried by everyone in that alliance.
Lives in the shared ``state.db`` alongside the other alliance tables
(``alliance_daily`` / ``alliance_members``), wired through :mod:`config.orm`
exactly like :mod:`games.wos.core.calendar.db`.

A read yields the *current* ready time for each trap, so :func:`upsert_traps`
replaces a trap's row (delete + insert on the composite key) rather than
accumulating history — there is exactly one "next ready" per trap.
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
_DEFAULT_WINDOW_MINUTES = 30  # a sprung trap's rally window ("lasts for 30 minutes")
_lock = threading.RLock()


class BearHuntTrapRow(SQLModel, table=True):
    __tablename__ = "bear_hunt_traps"

    game: str = Field(default=_DEFAULT_GAME, primary_key=True)
    alliance_name: str = Field(primary_key=True)
    trap_id: str = Field(primary_key=True)        # "1" / "2"
    ready_at: str                                 # ISO-8601 UTC — when the trap is next available
    level: int | None = Field(default=None)       # Trap Enhancement level (Lv. N; maxed at 5)
    window_minutes: int = Field(default=_DEFAULT_WINDOW_MINUTES)
    updated_at: float


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=[BearHuntTrapRow.__table__])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "bear_hunt", _ensure_schema)
    return engine


def upsert_traps(
    alliance_name: str,
    traps: dict[str, tuple[datetime, int | None]],
    *,
    game: str = _DEFAULT_GAME,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    now: float | None = None,
) -> int:
    """Replace each trap's ready time + level for an alliance. Returns rows written.

    ``traps`` is ``{trap_id: (ready_at, level)}``. Existing rows for the same
    ``(game, alliance_name, trap_id)`` are overwritten.
    """
    if not alliance_name or not traps:
        return 0
    ts = time.time() if now is None else now
    rows = [
        BearHuntTrapRow(
            game=game,
            alliance_name=alliance_name,
            trap_id=str(trap_id),
            ready_at=ready_at.isoformat(),
            level=level,
            window_minutes=window_minutes,
            updated_at=ts,
        )
        for trap_id, (ready_at, level) in traps.items()
    ]
    with _lock, Session(_engine()) as session:
        for row in rows:
            session.exec(
                delete(BearHuntTrapRow).where(
                    BearHuntTrapRow.game == game,
                    BearHuntTrapRow.alliance_name == alliance_name,
                    BearHuntTrapRow.trap_id == row.trap_id,
                )
            )
        session.add_all(rows)
        session.commit()
    return len(rows)


def get_traps(alliance_name: str, *, game: str = _DEFAULT_GAME) -> list[BearHuntTrapRow]:
    """All stored trap rows for an alliance, ordered by trap id."""
    if not alliance_name:
        return []
    with _lock, Session(_engine()) as session:
        stmt = (
            select(BearHuntTrapRow)
            .where(
                BearHuntTrapRow.game == game,
                BearHuntTrapRow.alliance_name == alliance_name,
            )
            .order_by(BearHuntTrapRow.trap_id)
        )
        return list(session.exec(stmt).all())
