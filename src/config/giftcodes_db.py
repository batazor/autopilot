"""SQLModel persistence for gift codes and per-player redemption status.

Schema (one row per (game, code) and (game, code, player)):

    gift_codes(game, name, expires_at, last_api_err_code, last_api_msg, updated_at,
               PRIMARY KEY(game, name))
    gift_code_redemptions(game, code_name, player_id, status, attempted_at,
                          PRIMARY KEY(game, code_name, player_id),
                          FK (game, code_name) → gift_codes(game, name))
    gift_code_settings(key, value, updated_at, PRIMARY KEY(key))

``game`` is ``'wos'`` or ``'kingshot'``; legacy rows (single-game era) are
migrated to ``'wos'`` on first connect.

Shares one ``state.db`` (and one SQLModel engine, see ``config.orm``) with
``state_sqlite`` / ``devices_db``.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKeyConstraint, Index, func
from sqlmodel import Field, Session, SQLModel, select

from config import orm
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable

    from sqlalchemy.engine import Engine

    from century.gift_codes.models import GiftCode, RedeemStatus

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_DEFAULT_GAME = "wos"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class GiftCodeRow(SQLModel, table=True):
    __tablename__ = "gift_codes"

    game: str = Field(default="wos", primary_key=True)
    name: str = Field(primary_key=True)
    expires_at: str | None = Field(default=None)
    last_api_err_code: int | None = Field(default=None)
    last_api_msg: str | None = Field(default=None)
    updated_at: float


class GiftCodeRedemption(SQLModel, table=True):
    __tablename__ = "gift_code_redemptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game", "code_name"],
            ["gift_codes.game", "gift_codes.name"],
            ondelete="CASCADE",
        ),
        Index("idx_gift_code_redemptions_code", "game", "code_name"),
    )

    game: str = Field(default="wos", primary_key=True)
    code_name: str = Field(primary_key=True)
    player_id: str = Field(primary_key=True)
    status: str
    attempted_at: float


class GiftCodeExternalGamer(SQLModel, table=True):
    __tablename__ = "gift_code_external_gamers"
    __table_args__ = (
        Index("idx_gift_code_external_gamers_enabled", "game", "enabled"),
    )

    game: str = Field(primary_key=True)
    player_id: int = Field(primary_key=True)
    nickname: str = Field(default="")
    label: str = Field(default="")
    enabled: int = Field(default=1)
    added_at: float
    last_seen_at: float | None = Field(default=None)


class GiftCodeSetting(SQLModel, table=True):
    __tablename__ = "gift_code_settings"

    key: str = Field(primary_key=True)
    value: str = Field(default="")
    updated_at: float


# ---------------------------------------------------------------------------
# schema setup + legacy migration
# ---------------------------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    """One-shot migration from the single-game schema (no ``game`` column).

    Detected by absence of the ``game`` column. Existing rows are stamped
    ``'wos'`` because that's the only game the previous version supported.
    """
    cols = _table_columns(conn, "gift_codes")
    if cols and "game" not in cols:
        logger.info("migrating gift_codes to multi-game schema (defaulting existing rows to 'wos')")
        conn.executescript("""
            CREATE TABLE gift_codes__new (
                game TEXT NOT NULL DEFAULT 'wos',
                name TEXT NOT NULL,
                expires_at TEXT,
                last_api_err_code INTEGER,
                last_api_msg TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (game, name)
            );
            INSERT INTO gift_codes__new (game, name, expires_at, last_api_err_code, last_api_msg, updated_at)
                SELECT 'wos', name, expires_at, last_api_err_code, last_api_msg, updated_at
                FROM gift_codes;
            DROP TABLE gift_codes;
            ALTER TABLE gift_codes__new RENAME TO gift_codes;
        """)

    cols = _table_columns(conn, "gift_code_redemptions")
    if cols and "game" not in cols:
        logger.info("migrating gift_code_redemptions to multi-game schema")
        conn.executescript("""
            CREATE TABLE gift_code_redemptions__new (
                game TEXT NOT NULL DEFAULT 'wos',
                code_name TEXT NOT NULL,
                player_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at REAL NOT NULL,
                PRIMARY KEY (game, code_name, player_id),
                FOREIGN KEY (game, code_name) REFERENCES gift_codes(game, name) ON DELETE CASCADE
            );
            INSERT INTO gift_code_redemptions__new (game, code_name, player_id, status, attempted_at)
                SELECT 'wos', code_name, player_id, status, attempted_at
                FROM gift_code_redemptions;
            DROP TABLE gift_code_redemptions;
            ALTER TABLE gift_code_redemptions__new RENAME TO gift_code_redemptions;
            CREATE INDEX IF NOT EXISTS idx_gift_code_redemptions_code
                ON gift_code_redemptions(game, code_name);
        """)


def _ensure_schema(engine: Engine) -> None:
    """Create missing tables, then run tracked legacy migrations."""
    SQLModel.metadata.create_all(
        engine,
        tables=[
            GiftCodeRow.__table__,
            GiftCodeRedemption.__table__,
            GiftCodeExternalGamer.__table__,
            GiftCodeSetting.__table__,
        ],
    )
    orm.apply_migrations(engine, "giftcodes", [
        ("001_multigame", _migrate_legacy_schema),
    ])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "giftcodes", _ensure_schema)
    return engine


# ---------------------------------------------------------------------------
# write helpers
# ---------------------------------------------------------------------------


def upsert_code(
    name: str,
    *,
    game: str = _DEFAULT_GAME,
    expires: datetime | None = None,
    last_api_err_code: int | None = None,
    last_api_msg: str | None = None,
) -> None:
    """Insert or update a gift code's metadata. Non-None args overwrite.

    ``last_api_err_code`` / ``last_api_msg`` are passed through verbatim — to
    *clear* a stale message, pass an empty string or 0 explicitly (None means
    "don't touch this field").
    """
    now = time.time()
    expires_iso = expires.isoformat() if expires is not None else None
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeRow, (game, name))
        if row is None:
            s.add(GiftCodeRow(
                game=game, name=name, expires_at=expires_iso,
                last_api_err_code=last_api_err_code, last_api_msg=last_api_msg,
                updated_at=now,
            ))
        else:
            if expires is not None:
                row.expires_at = expires_iso
            if last_api_err_code is not None:
                row.last_api_err_code = last_api_err_code
            if last_api_msg is not None:
                row.last_api_msg = last_api_msg
            row.updated_at = now
            s.add(row)
        s.commit()


def set_redemption(
    code_name: str, player_id: str, status: RedeemStatus, *, game: str = _DEFAULT_GAME
) -> None:
    """Record a redemption attempt. Sticky terminal statuses are not enforced
    here — callers (the redeemer) decide what to write; the schema only stores."""
    now = time.time()
    pid = str(player_id)
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeRedemption, (game, code_name, pid))
        if row is None:
            s.add(GiftCodeRedemption(
                game=game, code_name=code_name, player_id=pid,
                status=status.value, attempted_at=now,
            ))
        else:
            row.status = status.value
            row.attempted_at = now
            s.add(row)
        s.commit()


def set_redemption_bulk(
    code_name: str,
    player_ids: Iterable[str],
    status: RedeemStatus,
    *,
    game: str = _DEFAULT_GAME,
) -> None:
    """Stamp the same terminal status for every listed player on one code.
    Used when CDK_EXPIRED / CDK_NOT_FOUND comes back — code is dead globally."""
    now = time.time()
    pids = [str(pid) for pid in player_ids]
    if not pids:
        return
    with _conn_lock, Session(_engine()) as s:
        for pid in pids:
            row = s.get(GiftCodeRedemption, (game, code_name, pid))
            if row is None:
                s.add(GiftCodeRedemption(
                    game=game, code_name=code_name, player_id=pid,
                    status=status.value, attempted_at=now,
                ))
            else:
                row.status = status.value
                row.attempted_at = now
                s.add(row)
        s.commit()


def delete_code(name: str, *, game: str = _DEFAULT_GAME) -> None:
    """Remove a code (cascades redemptions). Used by tests / manual cleanup."""
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeRow, (game, name))
        if row is not None:
            s.delete(row)  # ON DELETE CASCADE (foreign_keys=ON) clears redemptions
            s.commit()


# ---------------------------------------------------------------------------
# read helpers
# ---------------------------------------------------------------------------


def _parse_expires(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def list_codes(*, game: str | None = _DEFAULT_GAME) -> list[GiftCode]:
    """Load codes with aggregated per-player redemption status.

    Pass ``game=None`` to load every game's codes (used by the dashboard).
    Pass an explicit ID to filter to one game (the redeemer's normal mode).
    """
    from century.gift_codes.models import GiftCode, RedeemStatus

    with Session(_engine()) as s:
        code_stmt = select(GiftCodeRow)
        red_stmt = select(GiftCodeRedemption)
        if game is not None:
            code_stmt = code_stmt.where(GiftCodeRow.game == game)
            red_stmt = red_stmt.where(GiftCodeRedemption.game == game)
        code_stmt = code_stmt.order_by(GiftCodeRow.updated_at.desc())
        code_rows = s.exec(code_stmt).all()
        red_rows = s.exec(red_stmt).all()

    by_code: dict[tuple[str, str], dict[str, RedeemStatus]] = {}
    for r in red_rows:
        try:
            status = RedeemStatus(r.status)
        except ValueError:
            status = RedeemStatus.PENDING
        by_code.setdefault((r.game, r.code_name), {})[r.player_id] = status

    return [
        GiftCode(
            name=row.name,
            game=row.game,
            expires=_parse_expires(row.expires_at),
            user_for=by_code.get((row.game, row.name), {}),
            last_api_err_code=row.last_api_err_code,
            last_api_msg=row.last_api_msg,
        )
        for row in code_rows
    ]


def get_redemption(
    code_name: str, player_id: str, *, game: str = _DEFAULT_GAME
) -> RedeemStatus | None:
    """Return the recorded status for one (code, player), or ``None`` if missing."""
    from century.gift_codes.models import RedeemStatus

    with Session(_engine()) as s:
        row = s.get(GiftCodeRedemption, (game, code_name, str(player_id)))
    if row is None:
        return None
    try:
        return RedeemStatus(row.status)
    except ValueError:
        return RedeemStatus.PENDING


def code_exists(name: str, *, game: str = _DEFAULT_GAME) -> bool:
    with Session(_engine()) as s:
        return s.get(GiftCodeRow, (game, name)) is not None


def count_codes(*, game: str | None = None) -> int:
    with Session(_engine()) as s:
        stmt = select(func.count()).select_from(GiftCodeRow)
        if game is not None:
            stmt = stmt.where(GiftCodeRow.game == game)
        return int(s.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# External gamers (Pro feature: gift_codes.external_accounts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalGamer:
    """One external account row — accounts the bot does not own.

    ``enabled=False`` keeps the row but excludes it from redemption runs
    (preserves history without forcing a delete). ``last_seen_at`` is set
    after a successful ``/api/player`` call by the redeemer / admission flow.
    """

    game: str
    player_id: int
    nickname: str = ""
    label: str = ""
    enabled: bool = True
    added_at: float = 0.0
    last_seen_at: float | None = None


def _row_to_external(row: GiftCodeExternalGamer) -> ExternalGamer:
    return ExternalGamer(
        game=row.game,
        player_id=int(row.player_id),
        nickname=row.nickname or "",
        label=row.label or "",
        enabled=bool(row.enabled),
        added_at=float(row.added_at or 0.0),
        last_seen_at=float(row.last_seen_at) if row.last_seen_at is not None else None,
    )


def upsert_external_gamer(
    player_id: int | str,
    *,
    game: str = _DEFAULT_GAME,
    nickname: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
) -> ExternalGamer:
    """Insert or update an external gamer. Returns the row after the write.

    Non-``None`` arguments overwrite; passing ``None`` preserves the existing
    value (handy for partial updates from the UI).
    """
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError) as exc:
        msg = f"invalid player_id: {player_id!r}"
        raise ValueError(msg) from exc
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeExternalGamer, (game, pid))
        if row is None:
            row = GiftCodeExternalGamer(
                game=game, player_id=pid,
                nickname=nickname or "", label=label or "",
                enabled=1 if (enabled is not False) else 0, added_at=now,
            )
        else:
            if nickname is not None:
                row.nickname = nickname
            if label is not None:
                row.label = label
            if enabled is not None:
                row.enabled = 1 if enabled else 0
        s.add(row)
        s.commit()
        s.refresh(row)
        return _row_to_external(row)


# ---------------------------------------------------------------------------
# Gift-code runtime settings (stored in encrypted state.db)
# ---------------------------------------------------------------------------


def get_gift_code_setting(key: str, default: str = "") -> str:
    clean = str(key or "").strip()
    if not clean:
        return default
    with Session(_engine()) as s:
        row = s.get(GiftCodeSetting, clean)
        if row is None:
            return default
        return row.value or ""


def set_gift_code_setting(key: str, value: str | None) -> None:
    clean = str(key or "").strip()
    if not clean:
        msg = "gift-code setting key is required"
        raise ValueError(msg)
    raw = "" if value is None else str(value)
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeSetting, clean)
        if row is None:
            row = GiftCodeSetting(key=clean, value=raw, updated_at=now)
        else:
            row.value = raw
            row.updated_at = now
        s.add(row)
        s.commit()


def delete_gift_code_setting(key: str) -> None:
    clean = str(key or "").strip()
    if not clean:
        return
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeSetting, clean)
        if row is None:
            return
        s.delete(row)
        s.commit()


def delete_external_gamer(player_id: int | str, *, game: str = _DEFAULT_GAME) -> bool:
    """Remove an external gamer row. Returns True iff a row was deleted."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return False
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeExternalGamer, (game, pid))
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def set_external_gamer_enabled(
    player_id: int | str, enabled: bool, *, game: str = _DEFAULT_GAME
) -> bool:
    """Toggle the ``enabled`` flag on an existing row. Returns True iff updated."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return False
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeExternalGamer, (game, pid))
        if row is None:
            return False
        row.enabled = 1 if enabled else 0
        s.add(row)
        s.commit()
        return True


def touch_external_gamer_seen(
    player_id: int | str, *, game: str = _DEFAULT_GAME, when: float | None = None
) -> None:
    """Stamp ``last_seen_at`` (silent no-op if the row doesn't exist)."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return
    ts = when if when is not None else time.time()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(GiftCodeExternalGamer, (game, pid))
        if row is not None:
            row.last_seen_at = ts
            s.add(row)
            s.commit()


def list_external_gamers(
    *, game: str = _DEFAULT_GAME, enabled_only: bool = False
) -> list[ExternalGamer]:
    """Return every external gamer for ``game`` (optionally only enabled)."""
    with Session(_engine()) as s:
        stmt = select(GiftCodeExternalGamer).where(GiftCodeExternalGamer.game == game)
        if enabled_only:
            stmt = stmt.where(GiftCodeExternalGamer.enabled == 1)
        stmt = stmt.order_by(
            GiftCodeExternalGamer.added_at.asc(), GiftCodeExternalGamer.player_id.asc()
        )
        rows = s.exec(stmt).all()
    return [_row_to_external(r) for r in rows]


def count_external_gamers(*, game: str | None = None, enabled_only: bool = False) -> int:
    with Session(_engine()) as s:
        stmt = select(func.count()).select_from(GiftCodeExternalGamer)
        if game is not None:
            stmt = stmt.where(GiftCodeExternalGamer.game == game)
        if enabled_only:
            stmt = stmt.where(GiftCodeExternalGamer.enabled == 1)
        return int(s.scalar(stmt) or 0)
