"""SQLite persistence for gift codes and per-player redemption status.

Replaces the previous ``db/giftCodes.yaml`` file. Two tables:

    gift_codes(name PK, expires_at, last_api_err_code, last_api_msg, updated_at)
    gift_code_redemptions(code_name, player_id, status, attempted_at, PK(code_name, player_id))

The pydantic ``GiftCode`` model in ``modules.gift_codes.models`` stays as the
in-memory representation — callers build it from rows via ``list_codes()`` and
feed individual fields back via ``upsert_code()`` / ``set_redemption()``.

Shares the same SQLite file as the player state store
(``src/config/state_sqlite.state_db_path()``) so a single ``wos.db`` holds all
durable persistence.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from modules.gift_codes.models import GiftCode, RedeemStatus

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gift_codes (
    name TEXT PRIMARY KEY,
    expires_at TEXT,
    last_api_err_code INTEGER,
    last_api_msg TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gift_code_redemptions (
    code_name TEXT NOT NULL,
    player_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempted_at REAL NOT NULL,
    PRIMARY KEY (code_name, player_id),
    FOREIGN KEY (code_name) REFERENCES gift_codes(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gift_code_redemptions_code ON gift_code_redemptions(code_name);
"""


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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
            "SELECT expires_at, last_api_err_code, last_api_msg FROM gift_codes WHERE name = ?",
            (name,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO gift_codes (name, expires_at, last_api_err_code, last_api_msg, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, expires_iso, last_api_err_code, last_api_msg, now),
            )
        else:
            # Merge: only overwrite columns where the caller passed a non-None value.
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
                "last_api_msg = ?, updated_at = ? WHERE name = ?",
                (new_expires, new_err, new_msg, now, name),
            )
        conn.commit()


def set_redemption(code_name: str, player_id: str, status: RedeemStatus) -> None:
    """Record a redemption attempt. Sticky terminal statuses are not enforced
    here — callers (the redeemer) decide what to write; the schema only stores."""
    now = time.time()
    with _conn_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO gift_code_redemptions (code_name, player_id, status, attempted_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code_name, player_id) DO UPDATE SET "
            "status = excluded.status, attempted_at = excluded.attempted_at",
            (code_name, str(player_id), status.value, now),
        )
        conn.commit()


def set_redemption_bulk(
    code_name: str, player_ids: Iterable[str], status: RedeemStatus
) -> None:
    """Stamp the same terminal status for every listed player on one code.
    Used when CDK_EXPIRED / CDK_NOT_FOUND comes back — code is dead globally."""
    now = time.time()
    rows = [(code_name, str(pid), status.value, now) for pid in player_ids]
    if not rows:
        return
    with _conn_lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO gift_code_redemptions (code_name, player_id, status, attempted_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code_name, player_id) DO UPDATE SET "
            "status = excluded.status, attempted_at = excluded.attempted_at",
            rows,
        )
        conn.commit()


def delete_code(name: str) -> None:
    """Remove a code (cascades redemptions). Used by tests / manual cleanup."""
    with _conn_lock, _connect() as conn:
        conn.execute("DELETE FROM gift_codes WHERE name = ?", (name,))
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


def list_codes() -> list[GiftCode]:
    """Load every code with aggregated per-player redemption status.

    Returns ``GiftCode`` pydantic models so callers (API, redeemer) keep the
    same shape they used to get from YAML.
    """
    from modules.gift_codes.models import GiftCode, RedeemStatus

    with _conn_lock, _connect() as conn:
        code_rows = conn.execute(
            "SELECT name, expires_at, last_api_err_code, last_api_msg "
            "FROM gift_codes ORDER BY updated_at DESC"
        ).fetchall()
        red_rows = conn.execute(
            "SELECT code_name, player_id, status FROM gift_code_redemptions"
        ).fetchall()

    by_code: dict[str, dict[str, RedeemStatus]] = {}
    for r in red_rows:
        try:
            status = RedeemStatus(r["status"])
        except ValueError:
            status = RedeemStatus.PENDING
        by_code.setdefault(r["code_name"], {})[r["player_id"]] = status

    return [
        GiftCode(
            name=row["name"],
            expires=_parse_expires(row["expires_at"]),
            user_for=by_code.get(row["name"], {}),
            last_api_err_code=row["last_api_err_code"],
            last_api_msg=row["last_api_msg"],
        )
        for row in code_rows
    ]


def get_redemption(code_name: str, player_id: str) -> RedeemStatus | None:
    """Return the recorded status for one (code, player), or ``None`` if missing."""
    from modules.gift_codes.models import RedeemStatus

    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM gift_code_redemptions WHERE code_name = ? AND player_id = ?",
            (code_name, str(player_id)),
        ).fetchone()
    if row is None:
        return None
    try:
        return RedeemStatus(row["status"])
    except ValueError:
        return RedeemStatus.PENDING


def code_exists(name: str) -> bool:
    with _conn_lock, _connect() as conn:
        row = conn.execute("SELECT 1 FROM gift_codes WHERE name = ?", (name,)).fetchone()
    return row is not None


def count_codes() -> int:
    with _conn_lock, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM gift_codes").fetchone()
    return int(row["n"] or 0)


