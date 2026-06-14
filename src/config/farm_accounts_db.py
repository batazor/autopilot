"""SQLModel persistence for generated farm accounts (R5 / owner-only feature).

One row per (game, username). Tracks the generated credentials, the lifecycle
status, the in-game player id (``fid``) once known, and which emulator the
account is bound to.

    farm_accounts(game, username, password, email, fid, server, status,
                  device_serial, created_at, registered_at, note,
                  PRIMARY KEY(game, username))

Lifecycle ``status``:
  - ``pending``     — credentials generated, not yet registered on the server.
  - ``registered``  — registration completed (human solved the captcha); fid
                      may be filled once read in-game.
  - ``bound``       — assigned to / logged in on a specific emulator.
  - ``failed``      — a registration attempt failed.

Shares one encrypted ``state.db`` (and one SQLModel engine, see ``config.orm``)
with ``state_sqlite`` / ``devices_db`` / ``giftcodes_db``. Credentials live in
the SQLCipher-encrypted DB like every other secret the app owns.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import Field, Session, SQLModel, select

from config import orm
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_DEFAULT_GAME = "wos"
_DEFAULT_SERVER = "wos_beta"

STATUS_PENDING = "pending"
STATUS_REGISTERED = "registered"
STATUS_BOUND = "bound"
STATUS_FAILED = "failed"
_VALID_STATUS = frozenset(
    {STATUS_PENDING, STATUS_REGISTERED, STATUS_BOUND, STATUS_FAILED}
)


class FarmAccountRow(SQLModel, table=True):
    __tablename__ = "farm_accounts"

    game: str = Field(default=_DEFAULT_GAME, primary_key=True)
    username: str = Field(primary_key=True)
    password: str = Field(default="")
    email: str = Field(default="")
    fid: str | None = Field(default=None)
    server: str = Field(default=_DEFAULT_SERVER)
    status: str = Field(default=STATUS_PENDING)
    device_serial: str | None = Field(default=None)
    created_at: float = Field(default=0.0)
    registered_at: float | None = Field(default=None)
    note: str = Field(default="")


@dataclass(frozen=True)
class FarmAccount:
    game: str
    username: str
    password: str = ""
    email: str = ""
    fid: str | None = None
    server: str = _DEFAULT_SERVER
    status: str = STATUS_PENDING
    device_serial: str | None = None
    created_at: float = 0.0
    registered_at: float | None = None
    note: str = ""


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=[FarmAccountRow.__table__])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "farm_accounts", _ensure_schema)
    return engine


def _row_to_account(row: FarmAccountRow) -> FarmAccount:
    return FarmAccount(
        game=row.game,
        username=row.username,
        password=row.password or "",
        email=row.email or "",
        fid=row.fid,
        server=row.server or _DEFAULT_SERVER,
        status=row.status or STATUS_PENDING,
        device_serial=row.device_serial,
        created_at=float(row.created_at or 0.0),
        registered_at=(
            float(row.registered_at) if row.registered_at is not None else None
        ),
        note=row.note or "",
    )


def add_account(
    username: str,
    *,
    password: str,
    email: str = "",
    game: str = _DEFAULT_GAME,
    server: str = _DEFAULT_SERVER,
    note: str = "",
) -> FarmAccount:
    """Insert a freshly generated account in ``pending`` state.

    Raises ``ValueError`` on a (game, username) collision so the generator can
    retry with a fresh name instead of silently overwriting credentials.
    """
    uname = str(username or "").strip()
    if not uname:
        msg = "username is required"
        raise ValueError(msg)
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        if s.get(FarmAccountRow, (game, uname)) is not None:
            msg = f"farm account already exists: {game}/{uname}"
            raise ValueError(msg)
        row = FarmAccountRow(
            game=game,
            username=uname,
            password=password,
            email=email,
            server=server,
            status=STATUS_PENDING,
            created_at=now,
            note=note,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return _row_to_account(row)


def set_status(
    username: str,
    status: str,
    *,
    game: str = _DEFAULT_GAME,
    fid: str | None = None,
    note: str | None = None,
) -> bool:
    """Move an account to a new lifecycle status. Returns True iff a row updated.

    Stamps ``registered_at`` the first time it reaches ``registered``. ``fid``
    and ``note`` overwrite only when non-None.
    """
    clean = str(status or "").strip().lower()
    if clean not in _VALID_STATUS:
        msg = f"invalid status: {status!r} (expected one of {sorted(_VALID_STATUS)})"
        raise ValueError(msg)
    uname = str(username or "").strip()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(FarmAccountRow, (game, uname))
        if row is None:
            return False
        row.status = clean
        if clean == STATUS_REGISTERED and row.registered_at is None:
            row.registered_at = time.time()
        if fid is not None:
            row.fid = str(fid).strip() or None
        if note is not None:
            row.note = note
        s.add(row)
        s.commit()
        return True


def bind_device(
    username: str, device_serial: str, *, game: str = _DEFAULT_GAME
) -> bool:
    """Bind an account to an emulator serial and mark it ``bound``."""
    uname = str(username or "").strip()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(FarmAccountRow, (game, uname))
        if row is None:
            return False
        row.device_serial = str(device_serial).strip() or None
        row.status = STATUS_BOUND
        s.add(row)
        s.commit()
        return True


def get_account(username: str, *, game: str = _DEFAULT_GAME) -> FarmAccount | None:
    with Session(_engine()) as s:
        row = s.get(FarmAccountRow, (game, str(username or "").strip()))
    return _row_to_account(row) if row is not None else None


def username_exists(username: str, *, game: str = _DEFAULT_GAME) -> bool:
    with Session(_engine()) as s:
        return (
            s.get(FarmAccountRow, (game, str(username or "").strip())) is not None
        )


def list_accounts(
    *, game: str | None = _DEFAULT_GAME, status: str | None = None
) -> list[FarmAccount]:
    with Session(_engine()) as s:
        stmt = select(FarmAccountRow)
        if game is not None:
            stmt = stmt.where(FarmAccountRow.game == game)
        if status is not None:
            stmt = stmt.where(FarmAccountRow.status == status.strip().lower())
        stmt = stmt.order_by(FarmAccountRow.created_at.asc())
        rows = s.exec(stmt).all()
    return [_row_to_account(r) for r in rows]


def count_accounts(
    *, game: str | None = _DEFAULT_GAME, status: str | None = None
) -> int:
    with Session(_engine()) as s:
        stmt = select(func.count()).select_from(FarmAccountRow)
        if game is not None:
            stmt = stmt.where(FarmAccountRow.game == game)
        if status is not None:
            stmt = stmt.where(FarmAccountRow.status == status.strip().lower())
        return int(s.scalar(stmt) or 0)


def delete_account(username: str, *, game: str = _DEFAULT_GAME) -> bool:
    with _conn_lock, Session(_engine()) as s:
        row = s.get(FarmAccountRow, (game, str(username or "").strip()))
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True
