"""SQLite persistence for gift codes and per-player redemption status.

Schema (one row per (game, code) and (game, code, player)):

    gift_codes(game, name, expires_at, last_api_err_code, last_api_msg, updated_at,
               PRIMARY KEY(game, name))
    gift_code_redemptions(game, code_name, player_id, status, attempted_at,
                          PRIMARY KEY(game, code_name, player_id),
                          FK (game, code_name) → gift_codes(game, name))

``game`` is ``'wos'`` or ``'kingshot'``; legacy rows (single-game era) are
migrated to ``'wos'`` on first connect.

Shares one ``state.db`` with ``state_sqlite`` / ``devices_db``.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from games.wos.gift_codes.models import GiftCode, RedeemStatus

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_DEFAULT_GAME = "wos"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gift_codes (
    game TEXT NOT NULL DEFAULT 'wos',
    name TEXT NOT NULL,
    expires_at TEXT,
    last_api_err_code INTEGER,
    last_api_msg TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (game, name)
);

CREATE TABLE IF NOT EXISTS gift_code_redemptions (
    game TEXT NOT NULL DEFAULT 'wos',
    code_name TEXT NOT NULL,
    player_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempted_at REAL NOT NULL,
    PRIMARY KEY (game, code_name, player_id),
    FOREIGN KEY (game, code_name) REFERENCES gift_codes(game, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gift_code_redemptions_code ON gift_code_redemptions(game, code_name);

-- External gamers: accounts the bot does NOT own (alliance members,
-- partner farms, secondary accounts on hardware we don't run). Used by
-- the gift-code redeemer when the ``gift_codes.external_accounts`` Pro
-- feature is licensed. Independent of devices/profiles — these have no
-- emulator. Same ``(game, player_id)`` key shape as the durable state
-- tables so cross-table queries stay simple.
CREATE TABLE IF NOT EXISTS gift_code_external_gamers (
    game TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at REAL NOT NULL,
    last_seen_at REAL,
    PRIMARY KEY (game, player_id)
);

CREATE INDEX IF NOT EXISTS idx_gift_code_external_gamers_enabled
    ON gift_code_external_gamers(game, enabled);
"""


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


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate_legacy_schema(conn)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        yield conn
    finally:
        conn.close()


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
    with _conn_lock, _connect() as conn:
        existing = conn.execute(
            "SELECT expires_at, last_api_err_code, last_api_msg "
            "FROM gift_codes WHERE game = ? AND name = ?",
            (game, name),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO gift_codes (game, name, expires_at, last_api_err_code, "
                "last_api_msg, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (game, name, expires_iso, last_api_err_code, last_api_msg, now),
            )
        else:
            new_expires = expires_iso if expires is not None else existing["expires_at"]
            new_err = (
                last_api_err_code
                if last_api_err_code is not None
                else existing["last_api_err_code"]
            )
            new_msg = (
                last_api_msg if last_api_msg is not None else existing["last_api_msg"]
            )
            conn.execute(
                "UPDATE gift_codes SET expires_at = ?, last_api_err_code = ?, "
                "last_api_msg = ?, updated_at = ? WHERE game = ? AND name = ?",
                (new_expires, new_err, new_msg, now, game, name),
            )
        conn.commit()


def set_redemption(
    code_name: str, player_id: str, status: RedeemStatus, *, game: str = _DEFAULT_GAME
) -> None:
    """Record a redemption attempt. Sticky terminal statuses are not enforced
    here — callers (the redeemer) decide what to write; the schema only stores."""
    now = time.time()
    with _conn_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO gift_code_redemptions (game, code_name, player_id, status, attempted_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(game, code_name, player_id) DO UPDATE SET "
            "status = excluded.status, attempted_at = excluded.attempted_at",
            (game, code_name, str(player_id), status.value, now),
        )
        conn.commit()


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
    rows = [(game, code_name, str(pid), status.value, now) for pid in player_ids]
    if not rows:
        return
    with _conn_lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO gift_code_redemptions (game, code_name, player_id, status, attempted_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(game, code_name, player_id) DO UPDATE SET "
            "status = excluded.status, attempted_at = excluded.attempted_at",
            rows,
        )
        conn.commit()


def delete_code(name: str, *, game: str = _DEFAULT_GAME) -> None:
    """Remove a code (cascades redemptions). Used by tests / manual cleanup."""
    with _conn_lock, _connect() as conn:
        conn.execute("DELETE FROM gift_codes WHERE game = ? AND name = ?", (game, name))
        conn.commit()


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
    from games.wos.gift_codes.models import GiftCode, RedeemStatus

    with _conn_lock, _connect() as conn:
        if game is None:
            code_rows = conn.execute(
                "SELECT game, name, expires_at, last_api_err_code, last_api_msg "
                "FROM gift_codes ORDER BY updated_at DESC"
            ).fetchall()
            red_rows = conn.execute(
                "SELECT game, code_name, player_id, status FROM gift_code_redemptions"
            ).fetchall()
        else:
            code_rows = conn.execute(
                "SELECT game, name, expires_at, last_api_err_code, last_api_msg "
                "FROM gift_codes WHERE game = ? ORDER BY updated_at DESC",
                (game,),
            ).fetchall()
            red_rows = conn.execute(
                "SELECT game, code_name, player_id, status FROM gift_code_redemptions WHERE game = ?",
                (game,),
            ).fetchall()

    by_code: dict[tuple[str, str], dict[str, RedeemStatus]] = {}
    for r in red_rows:
        try:
            status = RedeemStatus(r["status"])
        except ValueError:
            status = RedeemStatus.PENDING
        by_code.setdefault((r["game"], r["code_name"]), {})[r["player_id"]] = status

    return [
        GiftCode(
            name=row["name"],
            game=row["game"],
            expires=_parse_expires(row["expires_at"]),
            user_for=by_code.get((row["game"], row["name"]), {}),
            last_api_err_code=row["last_api_err_code"],
            last_api_msg=row["last_api_msg"],
        )
        for row in code_rows
    ]


def get_redemption(
    code_name: str, player_id: str, *, game: str = _DEFAULT_GAME
) -> RedeemStatus | None:
    """Return the recorded status for one (code, player), or ``None`` if missing."""
    from games.wos.gift_codes.models import RedeemStatus

    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM gift_code_redemptions "
            "WHERE game = ? AND code_name = ? AND player_id = ?",
            (game, code_name, str(player_id)),
        ).fetchone()
    if row is None:
        return None
    try:
        return RedeemStatus(row["status"])
    except ValueError:
        return RedeemStatus.PENDING


def code_exists(name: str, *, game: str = _DEFAULT_GAME) -> bool:
    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM gift_codes WHERE game = ? AND name = ?", (game, name)
        ).fetchone()
    return row is not None


def count_codes(*, game: str | None = None) -> int:
    with _conn_lock, _connect() as conn:
        if game is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM gift_codes").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM gift_codes WHERE game = ?", (game,)
            ).fetchone()
    return int(row["n"] or 0)


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


def _row_to_external(row: sqlite3.Row) -> ExternalGamer:
    return ExternalGamer(
        game=row["game"],
        player_id=int(row["player_id"]),
        nickname=row["nickname"] or "",
        label=row["label"] or "",
        enabled=bool(row["enabled"]),
        added_at=float(row["added_at"] or 0.0),
        last_seen_at=float(row["last_seen_at"]) if row["last_seen_at"] is not None else None,
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
    with _conn_lock, _connect() as conn:
        existing = conn.execute(
            "SELECT nickname, label, enabled, added_at, last_seen_at "
            "FROM gift_code_external_gamers WHERE game = ? AND player_id = ?",
            (game, pid),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO gift_code_external_gamers "
                "(game, player_id, nickname, label, enabled, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (game, pid, nickname or "", label or "", 1 if (enabled is not False) else 0, now),
            )
        else:
            new_nick = nickname if nickname is not None else (existing["nickname"] or "")
            new_label = label if label is not None else (existing["label"] or "")
            new_enabled = 1 if (
                enabled if enabled is not None else bool(existing["enabled"])
            ) else 0
            conn.execute(
                "UPDATE gift_code_external_gamers "
                "SET nickname = ?, label = ?, enabled = ? "
                "WHERE game = ? AND player_id = ?",
                (new_nick, new_label, new_enabled, game, pid),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM gift_code_external_gamers WHERE game = ? AND player_id = ?",
            (game, pid),
        ).fetchone()
    return _row_to_external(row)


def delete_external_gamer(player_id: int | str, *, game: str = _DEFAULT_GAME) -> bool:
    """Remove an external gamer row. Returns True iff a row was deleted."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return False
    with _conn_lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM gift_code_external_gamers WHERE game = ? AND player_id = ?",
            (game, pid),
        )
        conn.commit()
    return cur.rowcount > 0


def set_external_gamer_enabled(
    player_id: int | str, enabled: bool, *, game: str = _DEFAULT_GAME
) -> bool:
    """Toggle the ``enabled`` flag on an existing row. Returns True iff updated."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return False
    with _conn_lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE gift_code_external_gamers SET enabled = ? "
            "WHERE game = ? AND player_id = ?",
            (1 if enabled else 0, game, pid),
        )
        conn.commit()
    return cur.rowcount > 0


def touch_external_gamer_seen(
    player_id: int | str, *, game: str = _DEFAULT_GAME, when: float | None = None
) -> None:
    """Stamp ``last_seen_at`` (silent no-op if the row doesn't exist)."""
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return
    ts = when if when is not None else time.time()
    with _conn_lock, _connect() as conn:
        conn.execute(
            "UPDATE gift_code_external_gamers SET last_seen_at = ? "
            "WHERE game = ? AND player_id = ?",
            (ts, game, pid),
        )
        conn.commit()


def list_external_gamers(
    *, game: str = _DEFAULT_GAME, enabled_only: bool = False
) -> list[ExternalGamer]:
    """Return every external gamer for ``game`` (optionally only enabled)."""
    sql = "SELECT * FROM gift_code_external_gamers WHERE game = ?"
    params: tuple[object, ...] = (game,)
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY added_at ASC, player_id ASC"
    with _conn_lock, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_external(r) for r in rows]


def count_external_gamers(*, game: str | None = None, enabled_only: bool = False) -> int:
    sql = "SELECT COUNT(*) AS n FROM gift_code_external_gamers"
    params: tuple[object, ...] = ()
    clauses: list[str] = []
    if game is not None:
        clauses.append("game = ?")
        params = (*params, game)
    if enabled_only:
        clauses.append("enabled = 1")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    with _conn_lock, _connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["n"] or 0)
