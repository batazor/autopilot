"""SQLModel persistence for generated farm accounts (R5 / owner-only feature).

One account row per (game, username), plus zero or more in-game characters
attached to that login. The account tracks credentials and lifecycle; the
character table tracks server-specific game identities.

    farm_accounts(game, username, password, email, server, status,
                  device_serial, created_at, registered_at, note,
                  PRIMARY KEY(game, username))

    farm_characters(game, username, server, fid, nickname, created_at,
                    updated_at, note, PRIMARY KEY(game, username, server))

Lifecycle ``status``:
  - ``pending``     — credentials generated, not yet registered on the server.
  - ``registered``  — registration completed (human solved the captcha);
                      characters may be filled once read in-game.
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
    server: str = Field(default=_DEFAULT_SERVER)
    status: str = Field(default=STATUS_PENDING)
    device_serial: str | None = Field(default=None)
    created_at: float = Field(default=0.0)
    registered_at: float | None = Field(default=None)
    note: str = Field(default="")


class FarmCharacterRow(SQLModel, table=True):
    __tablename__ = "farm_characters"

    game: str = Field(default=_DEFAULT_GAME, primary_key=True)
    username: str = Field(primary_key=True)
    server: str = Field(default=_DEFAULT_SERVER, primary_key=True)
    fid: str = Field(default="")
    nickname: str = Field(default="")
    created_at: float = Field(default=0.0)
    updated_at: float = Field(default=0.0)
    note: str = Field(default="")


@dataclass(frozen=True)
class FarmCharacter:
    game: str
    username: str
    server: str = _DEFAULT_SERVER
    fid: str = ""
    nickname: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class FarmAccount:
    game: str
    username: str
    password: str = ""
    email: str = ""
    server: str = _DEFAULT_SERVER
    status: str = STATUS_PENDING
    device_serial: str | None = None
    created_at: float = 0.0
    registered_at: float | None = None
    note: str = ""
    characters: tuple[FarmCharacter, ...] = ()


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(
        engine,
        tables=[FarmAccountRow.__table__, FarmCharacterRow.__table__],
    )
    orm.apply_migrations(
        engine,
        "farm_accounts",
        [("drop_legacy_fid_column", _drop_legacy_fid_column)],
    )


def _drop_legacy_fid_column(conn) -> None:  # noqa: ANN001 - sqlite/sqlcipher connection
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(farm_accounts)")]
    if "fid" not in cols:
        return
    conn.execute("DROP TABLE IF EXISTS farm_accounts_new")
    conn.execute(
        """
        CREATE TABLE farm_accounts_new (
            game TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            email TEXT NOT NULL,
            server TEXT NOT NULL,
            status TEXT NOT NULL,
            device_serial TEXT,
            created_at REAL NOT NULL,
            registered_at REAL,
            note TEXT NOT NULL,
            PRIMARY KEY (game, username)
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO farm_accounts_new (
            game, username, password, email, server, status, device_serial,
            created_at, registered_at, note
        )
        SELECT
            game, username, COALESCE(password, ''), COALESCE(email, ''),
            COALESCE(server, ?), COALESCE(status, ?), device_serial,
            COALESCE(created_at, 0.0), registered_at, COALESCE(note, '')
        FROM farm_accounts
        """,
        (_DEFAULT_SERVER, STATUS_PENDING),
    )
    conn.execute("DROP TABLE farm_accounts")
    conn.execute("ALTER TABLE farm_accounts_new RENAME TO farm_accounts")


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "farm_accounts_v2", _ensure_schema)
    return engine


def _row_to_character(row: FarmCharacterRow) -> FarmCharacter:
    return FarmCharacter(
        game=row.game,
        username=row.username,
        server=row.server or _DEFAULT_SERVER,
        fid=row.fid or "",
        nickname=row.nickname or "",
        created_at=float(row.created_at or 0.0),
        updated_at=float(row.updated_at or 0.0),
        note=row.note or "",
    )


def _characters_for_rows(
    rows: list[FarmAccountRow],
    chars: list[FarmCharacterRow],
) -> dict[tuple[str, str], tuple[FarmCharacter, ...]]:
    out: dict[tuple[str, str], list[FarmCharacter]] = {}
    for char in chars:
        out.setdefault((char.game, char.username), []).append(_row_to_character(char))
    return {key: tuple(value) for key, value in out.items()}


def _row_to_account(
    row: FarmAccountRow,
    *,
    characters: tuple[FarmCharacter, ...] = (),
) -> FarmAccount:
    return FarmAccount(
        game=row.game,
        username=row.username,
        password=row.password or "",
        email=row.email or "",
        server=row.server or _DEFAULT_SERVER,
        status=row.status or STATUS_PENDING,
        device_serial=row.device_serial,
        created_at=float(row.created_at or 0.0),
        registered_at=(
            float(row.registered_at) if row.registered_at is not None else None
        ),
        note=row.note or "",
        characters=characters,
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
    note: str | None = None,
) -> bool:
    """Move an account to a new lifecycle status. Returns True iff a row updated.

    Stamps ``registered_at`` the first time it reaches ``registered``. ``note``
    overwrites only when non-None.
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
        if note is not None:
            row.note = note
        s.add(row)
        s.commit()
        return True


def upsert_character(
    username: str,
    *,
    server: str,
    fid: str,
    game: str = _DEFAULT_GAME,
    nickname: str = "",
    note: str = "",
) -> FarmCharacter | None:
    """Insert or update the game character for this farm login on ``server``."""
    uname = str(username or "").strip()
    clean_server = str(server or "").strip()
    clean_fid = str(fid or "").strip()
    if not clean_server:
        msg = "server is required"
        raise ValueError(msg)
    if not clean_fid:
        msg = "fid is required"
        raise ValueError(msg)
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        account = s.get(FarmAccountRow, (game, uname))
        if account is None:
            return None
        row = s.get(FarmCharacterRow, (game, uname, clean_server))
        if row is None:
            row = FarmCharacterRow(
                game=game,
                username=uname,
                server=clean_server,
                created_at=now,
            )
        row.fid = clean_fid
        row.nickname = str(nickname or "").strip()
        row.note = str(note or "")
        row.updated_at = now
        s.add(row)
        s.commit()
        s.refresh(row)
        return _row_to_character(row)


def delete_character(
    username: str,
    *,
    server: str,
    game: str = _DEFAULT_GAME,
) -> bool:
    uname = str(username or "").strip()
    clean_server = str(server or "").strip()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(FarmCharacterRow, (game, uname, clean_server))
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def list_characters(
    username: str,
    *,
    game: str = _DEFAULT_GAME,
) -> list[FarmCharacter]:
    uname = str(username or "").strip()
    with Session(_engine()) as s:
        stmt = (
            select(FarmCharacterRow)
            .where(FarmCharacterRow.game == game)
            .where(FarmCharacterRow.username == uname)
            .order_by(FarmCharacterRow.server.asc())
        )
        rows = s.exec(stmt).all()
    return [_row_to_character(r) for r in rows]


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
        if row is None:
            return None
        chars = s.exec(
            select(FarmCharacterRow)
            .where(FarmCharacterRow.game == game)
            .where(FarmCharacterRow.username == row.username)
            .order_by(FarmCharacterRow.server.asc())
        ).all()
    return _row_to_account(row, characters=tuple(_row_to_character(c) for c in chars))


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
        char_stmt = select(FarmCharacterRow).order_by(
            FarmCharacterRow.username.asc(),
            FarmCharacterRow.server.asc(),
        )
        if game is not None:
            char_stmt = char_stmt.where(FarmCharacterRow.game == game)
        chars = s.exec(char_stmt).all()
    grouped = _characters_for_rows(rows, chars)
    return [
        _row_to_account(r, characters=grouped.get((r.game, r.username), ()))
        for r in rows
    ]


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
        chars = s.exec(
            select(FarmCharacterRow)
            .where(FarmCharacterRow.game == game)
            .where(FarmCharacterRow.username == row.username)
        ).all()
        for char in chars:
            s.delete(char)
        s.delete(row)
        s.commit()
        return True
