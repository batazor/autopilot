"""SQLModel persistence for device + profile + gamer registry.

Replaces ``db/devices.yaml``. Normalized schema across three tables:

    devices(name PK, adb_serial, screenshot_backend, input_backend,
            quartz_*, display_json, device_order, updated_at)
    device_profiles(id PK autoincrement, device_name FK, email, profile_order)
    device_profile_gamers(profile_id FK, player_id, nickname, level, gamer_order,
                          PK(profile_id, player_id))

Pydantic-ish dataclasses (``DeviceEntry``, ``DeviceProfile``, ``Gamer``,
``DeviceRegistry``) defined in ``config.devices`` stay the *external* contract;
callers continue to call ``load_devices()`` / ``upsert_device_gamer()`` exactly
as before — this module just swaps the storage backend underneath.

Shares the same SQLite file and SQLModel engine (see ``config.orm``) as
``state_sqlite`` / ``giftcodes_db`` (one ``state.db`` for all durable
persistence).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKeyConstraint, Index, func
from sqlmodel import Field, Session, SQLModel, col, select

from config import orm
from config.device_display import DeviceDisplayConfig
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    import sqlite3

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()


VALID_SCREENSHOT_BACKENDS = frozenset({"", "quartz", "adb", "scrcpy"})
VALID_INPUT_BACKENDS = frozenset({"", "adb", "scrcpy"})


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class DeviceRow(SQLModel, table=True):
    __tablename__ = "devices"

    name: str = Field(primary_key=True)
    adb_serial: str = Field(default="")
    screenshot_backend: str = Field(default="")
    input_backend: str = Field(default="")
    quartz_window_id: int | None = Field(default=None)
    quartz_window_title: str = Field(default="")
    quartz_crop_x: int | None = Field(default=None)
    quartz_crop_y: int | None = Field(default=None)
    quartz_crop_w: int | None = Field(default=None)
    quartz_crop_h: int | None = Field(default=None)
    display_json: str | None = Field(default=None)
    device_order: int = Field(default=0)
    game: str = Field(default="wos")
    last_active_player: str = Field(default="")
    updated_at: float


class DeviceProfileRow(SQLModel, table=True):
    __tablename__ = "device_profiles"
    __table_args__ = (
        ForeignKeyConstraint(["device_name"], ["devices.name"], ondelete="CASCADE"),
        Index("idx_device_profiles_device", "device_name", "profile_order"),
    )

    id: int | None = Field(default=None, primary_key=True)
    device_name: str
    email: str = Field(default="")
    profile_order: int = Field(default=0)
    game: str = Field(default="wos")


class DeviceProfileGamerRow(SQLModel, table=True):
    __tablename__ = "device_profile_gamers"
    __table_args__ = (
        ForeignKeyConstraint(["profile_id"], ["device_profiles.id"], ondelete="CASCADE"),
        Index("idx_device_profile_gamers_profile", "profile_id", "gamer_order"),
    )

    profile_id: int = Field(primary_key=True)
    player_id: int = Field(primary_key=True)
    nickname: str = Field(default="")
    level: int = Field(default=0)
    gamer_order: int = Field(default=0)
    game_package: str = Field(default="")


# ---------------------------------------------------------------------------
# schema setup + legacy migrations
# ---------------------------------------------------------------------------


# Columns added to each table over time. ``SQLModel.metadata.create_all`` only
# *creates* missing tables — it never ALTERs an existing one — so legacy
# ``state.db`` files keep whatever columns they were born with. We bring them up
# to the current schema by adding any column the model has but the table lacks.
# Every entry must be nullable or carry a DEFAULT (SQLite can't ALTER-add a bare
# NOT NULL column). ``name``/``id``/``updated_at`` are never listed — they exist
# from the table's creation and can't be added after the fact.
_COLUMN_DDL: dict[str, dict[str, str]] = {
    "devices": {
        "adb_serial": "TEXT NOT NULL DEFAULT ''",
        "screenshot_backend": "TEXT NOT NULL DEFAULT ''",
        "input_backend": "TEXT NOT NULL DEFAULT ''",
        "quartz_window_id": "INTEGER",
        "quartz_window_title": "TEXT NOT NULL DEFAULT ''",
        "quartz_crop_x": "INTEGER",
        "quartz_crop_y": "INTEGER",
        "quartz_crop_w": "INTEGER",
        "quartz_crop_h": "INTEGER",
        "display_json": "TEXT",
        "device_order": "INTEGER NOT NULL DEFAULT 0",
        # Backfills to 'wos' — the only game before multi-game support.
        "game": "TEXT NOT NULL DEFAULT 'wos'",
        # Durable active-player id so the worker can skip the who_i_am probe.
        "last_active_player": "TEXT NOT NULL DEFAULT ''",
    },
    "device_profiles": {
        "email": "TEXT NOT NULL DEFAULT ''",
        "profile_order": "INTEGER NOT NULL DEFAULT 0",
        "game": "TEXT NOT NULL DEFAULT 'wos'",
    },
    "device_profile_gamers": {
        "nickname": "TEXT NOT NULL DEFAULT ''",
        "level": "INTEGER NOT NULL DEFAULT 0",
        "gamer_order": "INTEGER NOT NULL DEFAULT 0",
        # Pins each account to its Android build (canonical vs beta alias).
        "game_package": "TEXT NOT NULL DEFAULT ''",
    },
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any model column missing from a pre-existing (legacy) table."""
    for table, columns in _COLUMN_DDL.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table absent — create_all already built it with all columns
        for col_name, ddl in columns.items():
            if col_name not in existing:
                logger.info("migrating %s: adding missing column %s", table, col_name)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {ddl}")


def _normalize_backends(conn: sqlite3.Connection) -> None:
    """Rewrite removed backends to scrcpy so legacy device rows stay bootable."""
    # Minicap was removed in favor of scrcpy.
    conn.execute(
        "UPDATE devices SET screenshot_backend = 'scrcpy' WHERE screenshot_backend = 'minicap'"
    )
    # Minitouch was removed; scrcpy is the replacement fast input backend.
    conn.execute(
        "UPDATE devices SET input_backend = 'scrcpy' WHERE input_backend = 'minitouch'"
    )


def _ensure_schema(engine: Engine) -> None:
    """Create missing tables, then run tracked legacy migrations."""
    SQLModel.metadata.create_all(
        engine,
        tables=[
            DeviceRow.__table__,
            DeviceProfileRow.__table__,
            DeviceProfileGamerRow.__table__,
        ],
    )
    orm.apply_migrations(engine, "devices", [
        ("001_backfill_columns", _ensure_columns),
        ("002_normalize_backends", _normalize_backends),
    ])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "devices", _ensure_schema)
    return engine


def _serialize_display(display: DeviceDisplayConfig | None) -> str | None:
    if display is None:
        return None
    return json.dumps(asdict(display))


def _deserialize_display(raw: str | None) -> DeviceDisplayConfig | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return DeviceDisplayConfig(**data)
    except TypeError:
        # Unknown field after schema drift — strip extras and try again.
        valid_fields = set(DeviceDisplayConfig.__dataclass_fields__.keys())
        return DeviceDisplayConfig(**{k: v for k, v in data.items() if k in valid_fields})


# ---------------------------------------------------------------------------
# device-level CRUD
# ---------------------------------------------------------------------------


def upsert_device(
    name: str,
    *,
    adb_serial: str = "",
    screenshot_backend: str = "",
    input_backend: str = "",
    quartz_window_id: int | None = None,
    quartz_window_title: str = "",
    quartz_crop: tuple[int, int, int, int] | None = None,
    display: DeviceDisplayConfig | None = None,
    device_order: int = 0,
    game: str | None = None,
) -> None:
    """Insert or replace a device row (does NOT touch profiles/gamers).

    ``game`` defaults to ``'wos'`` for new rows and is preserved for existing
    rows when omitted — pass an explicit value to switch games.
    """
    from config.games import default_game, is_known_game

    name = (name or "").strip()
    if not name:
        msg = "device name is required"
        raise ValueError(msg)
    if game is not None and not is_known_game(game):
        msg = f"unknown game id: {game!r}"
        raise ValueError(msg)
    screenshot_backend_clean = screenshot_backend.strip().lower()
    if screenshot_backend_clean not in VALID_SCREENSHOT_BACKENDS:
        msg = f"screenshot_backend must be one of {sorted(VALID_SCREENSHOT_BACKENDS - {''})} or empty"
        raise ValueError(msg)
    input_backend_clean = input_backend.strip().lower()
    if input_backend_clean not in VALID_INPUT_BACKENDS:
        msg = f"input_backend must be one of {sorted(VALID_INPUT_BACKENDS - {''})} or empty"
        raise ValueError(msg)
    now = time.time()
    crop = quartz_crop or (None, None, None, None)
    game_value = (game or default_game()).strip()
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DeviceRow, name)
        # When ``game`` is omitted, keep whatever the existing row had (or fall
        # back to default_game() for new rows).
        if game is None and row is not None:
            game_value = row.game
        if row is None:
            row = DeviceRow(name=name)
        row.adb_serial = adb_serial.strip()
        row.screenshot_backend = screenshot_backend_clean
        row.input_backend = input_backend_clean
        row.quartz_window_id = quartz_window_id
        row.quartz_window_title = quartz_window_title.strip()
        row.quartz_crop_x, row.quartz_crop_y, row.quartz_crop_w, row.quartz_crop_h = crop
        row.display_json = _serialize_display(display)
        row.device_order = device_order
        row.game = game_value
        row.updated_at = now
        s.add(row)
        s.commit()


def set_device_game(name: str, game: str) -> str:
    """Update only the ``game`` field on an existing device. Returns the new value.

    Raises ``KeyError`` if the device doesn't exist or ``ValueError`` if
    ``game`` is not in the registry.
    """
    from config.games import is_known_game

    name = (name or "").strip()
    if not name:
        msg = "device name is required"
        raise ValueError(msg)
    if not is_known_game(game):
        msg = f"unknown game id: {game!r}"
        raise ValueError(msg)
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DeviceRow, name)
        if row is None:
            msg = f"device not found: {name!r}"
            raise KeyError(msg)
        row.game = game
        row.updated_at = time.time()
        s.add(row)
        s.commit()
    return game


def set_profile_game(profile_id: int, game: str) -> str:
    """Override the per-profile game (defaults to the device's game otherwise)."""
    from config.games import is_known_game

    if not is_known_game(game):
        msg = f"unknown game id: {game!r}"
        raise ValueError(msg)
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DeviceProfileRow, int(profile_id))
        if row is None:
            msg = f"profile not found: {profile_id!r}"
            raise KeyError(msg)
        row.game = game
        s.add(row)
        s.commit()
    return game


def delete_device(name: str) -> bool:
    """Delete a device row (cascades to profiles + gamers). Returns True iff a row was removed."""
    with _conn_lock, Session(_engine()) as s:
        row = s.get(DeviceRow, (name or "").strip())
        if row is None:
            return False
        s.delete(row)  # ON DELETE CASCADE clears profiles + gamers
        s.commit()
        return True


def device_exists(name: str) -> bool:
    with _conn_lock, Session(_engine()) as s:
        return s.get(DeviceRow, (name or "").strip()) is not None


def count_devices() -> int:
    with _conn_lock, Session(_engine()) as s:
        return int(s.scalar(select(func.count()).select_from(DeviceRow)) or 0)


def set_device_backend(
    name: str,
    *,
    screenshot_backend: str | None = None,
    input_backend: str | None = None,
) -> tuple[str, str]:
    """Update only the backend fields. Returns the resulting (screenshot, input) pair.

    Pass an empty string to clear an override (smart default kicks in). Pass
    ``None`` to leave the field untouched.
    """
    name = (name or "").strip()
    if not name:
        msg = "device name is required"
        raise ValueError(msg)
    if screenshot_backend is not None:
        screenshot_backend = screenshot_backend.strip().lower()
        if screenshot_backend not in VALID_SCREENSHOT_BACKENDS:
            msg = f"screenshot_backend must be one of {sorted(VALID_SCREENSHOT_BACKENDS - {''})} or empty"
            raise ValueError(msg)
    if input_backend is not None:
        input_backend = input_backend.strip().lower()
        if input_backend not in VALID_INPUT_BACKENDS:
            msg = f"input_backend must be one of {sorted(VALID_INPUT_BACKENDS - {''})} or empty"
            raise ValueError(msg)

    with _conn_lock, Session(_engine()) as s:
        row = s.get(DeviceRow, name)
        if row is None:
            msg = f"device not found: {name!r}"
            raise KeyError(msg)
        new_screenshot = screenshot_backend if screenshot_backend is not None else row.screenshot_backend
        new_input = input_backend if input_backend is not None else row.input_backend
        row.screenshot_backend = new_screenshot
        row.input_backend = new_input
        row.updated_at = time.time()
        s.add(row)
        s.commit()
    return new_screenshot, new_input


# ---------------------------------------------------------------------------
# profile + gamer mutation (the path DSL `fetch_player` exercises)
# ---------------------------------------------------------------------------


def _find_canonical(s: Session, device_name_or_serial: str) -> str | None:
    """Match on either name or adb_serial — callers may pass whichever they have."""
    candidate = device_name_or_serial.strip()
    if not candidate:
        return None
    return s.exec(
        select(DeviceRow.name)
        .where((DeviceRow.name == candidate) | (DeviceRow.adb_serial == candidate))
        .limit(1)
    ).first()


# ---------------------------------------------------------------------------
# durable active-player identity (survives worker restarts → skip who_i_am)
# ---------------------------------------------------------------------------


def set_last_active_player(device_name: str, player_id: str) -> bool:
    """Persist the player id last identified on *device_name*.

    Matches the device row by name OR adb_serial (whichever the caller has).
    No-ops (and returns False) when the device is unknown or the stored value
    is already current. Returns True iff a row was actually updated.
    """
    device_name = (device_name or "").strip()
    player_id = (player_id or "").strip()
    if not device_name or not player_id:
        return False
    with _conn_lock, Session(_engine()) as s:
        canonical = _find_canonical(s, device_name)
        if canonical is None:
            return False
        row = s.get(DeviceRow, canonical)
        if row is not None and (row.last_active_player or "") == player_id:
            return False
        row.last_active_player = player_id
        row.updated_at = time.time()
        s.add(row)
        s.commit()
    return True


def set_gamer_package(player_id: str | int, package: str) -> bool:
    """Pin the registered account ``player_id`` to the build it runs on.

    Writes ``game_package`` for every ``device_profile_gamers`` row with this
    player id (a player id is unique to one account, so this is normally one
    row). No-ops when the account isn't registered or the value is unchanged.
    Returns True iff a row was updated.
    """
    package = (package or "").strip()
    try:
        pid = int(str(player_id).strip())
    except (TypeError, ValueError):
        return False
    if not package:
        return False
    with _conn_lock, Session(_engine()) as s:
        rows = s.exec(
            select(DeviceProfileGamerRow).where(
                DeviceProfileGamerRow.player_id == pid,
                DeviceProfileGamerRow.game_package != package,
            )
        ).all()
        if not rows:
            return False
        for row in rows:
            row.game_package = package
            s.add(row)
        s.commit()
        return True


def clear_last_active_player(device_name: str, player_id: str = "") -> bool:
    """Clear the durable active player for ``device_name``.

    When ``player_id`` is provided, clear only if the stored value still matches.
    This avoids wiping a newer identity written by a fresh ``who_i_am`` probe.
    """
    device_name = (device_name or "").strip()
    player_id = (player_id or "").strip()
    if not device_name:
        return False
    with _conn_lock, Session(_engine()) as s:
        canonical = _find_canonical(s, device_name)
        if canonical is None:
            return False
        row = s.get(DeviceRow, canonical)
        stored = (row.last_active_player or "").strip() if row else ""
        if not stored:
            return False
        if player_id and stored != player_id:
            return False
        row.last_active_player = ""
        row.updated_at = time.time()
        s.add(row)
        s.commit()
    return True


def get_last_active_player(*device_candidates: str) -> str:
    """Return the stored ``last_active_player`` for the first matching device alias.

    Callers pass whatever device handles they have (instance_id, adb serial,
    window title); the first one that resolves to a device row with a non-empty
    stored id wins. Returns ``""`` when nothing is found.
    """
    seen: set[str] = set()
    with _conn_lock, Session(_engine()) as s:
        for raw in device_candidates:
            candidate = str(raw or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            canonical = _find_canonical(s, candidate)
            if canonical is None:
                continue
            row = s.get(DeviceRow, canonical)
            stored = (row.last_active_player or "").strip() if row else ""
            if stored:
                return stored
    return ""


def upsert_device_gamer(
    device_name: str,
    player_id: str,
    nickname: str,
    *,
    email: str = "",
) -> bool:
    """Ensure ``player_id`` exists under ``device_name``'s first profile.

    - Creates the device row if missing.
    - Creates the first profile (with ``email``) if the device has none.
    - Updates the nickname if it differs from what's on file.
    - Returns True iff something actually changed (the legacy contract).
    """
    device_name = (device_name or "").strip()
    player_id = (player_id or "").strip()
    if not device_name or not player_id:
        return False
    try:
        pid_int = int(player_id)
    except ValueError:
        return False

    now = time.time()
    changed = False
    with _conn_lock, Session(_engine()) as s:
        canonical = _find_canonical(s, device_name)
        if canonical is None:
            s.add(DeviceRow(name=device_name, updated_at=now))
            s.commit()
            canonical = device_name
            changed = True

        profile = s.exec(
            select(DeviceProfileRow)
            .where(DeviceProfileRow.device_name == canonical)
            .order_by(DeviceProfileRow.profile_order, DeviceProfileRow.id)
            .limit(1)
        ).first()
        if profile is None:
            # New profile inherits the device's game so the registry stays
            # internally consistent without forcing every caller to know it.
            device = s.get(DeviceRow, canonical)
            device_game = (device.game if device else "wos") or "wos"
            profile = DeviceProfileRow(
                device_name=canonical, email=email, profile_order=0, game=device_game
            )
            s.add(profile)
            s.commit()
            s.refresh(profile)
            changed = True
        profile_id = profile.id

        gamer = s.get(DeviceProfileGamerRow, (profile_id, pid_int))
        if gamer is None:
            # Append at the end — preserve insertion order.
            max_order = s.scalar(
                select(func.max(DeviceProfileGamerRow.gamer_order))
                .where(DeviceProfileGamerRow.profile_id == profile_id)
            )
            next_order = int(max_order) + 1 if max_order is not None else 0
            s.add(DeviceProfileGamerRow(
                profile_id=profile_id, player_id=pid_int,
                nickname=nickname or "", level=0, gamer_order=next_order,
            ))
            changed = True
        else:
            current_nick = gamer.nickname or ""
            if nickname and nickname != current_nick:
                gamer.nickname = nickname
                s.add(gamer)
                changed = True

        if changed:
            device = s.get(DeviceRow, canonical)
            if device is not None:
                device.updated_at = now
                s.add(device)
        s.commit()
    return changed


def delete_device_gamer(player_id: str | int) -> int:
    """Remove ``player_id`` from every device profile.

    Returns the number of device_profile_gamers rows deleted. Empty profiles are
    left behind (they're cheap and `upsert_device_gamer` reuses them).
    """
    try:
        pid_int = int(str(player_id).strip())
    except (TypeError, ValueError):
        return 0
    now = time.time()
    with _conn_lock, Session(_engine()) as s:
        gamer_rows = s.exec(
            select(DeviceProfileGamerRow).where(DeviceProfileGamerRow.player_id == pid_int)
        ).all()
        deleted = len(gamer_rows)
        profile_ids = {g.profile_id for g in gamer_rows}
        affected_devices: set[str] = set()
        if profile_ids:
            affected_devices = set(s.exec(
                select(DeviceProfileRow.device_name)
                .where(col(DeviceProfileRow.id).in_(profile_ids))
            ).all())
        for gamer in gamer_rows:
            s.delete(gamer)
        for name in affected_devices:
            device = s.get(DeviceRow, name)
            if device is not None:
                device.updated_at = now
                s.add(device)
        s.commit()
    return deleted


# ---------------------------------------------------------------------------
# bulk load → DeviceRegistry
# ---------------------------------------------------------------------------


def _load_registry_rows() -> tuple[
    list[DeviceRow], list[DeviceProfileRow], list[DeviceProfileGamerRow]
]:
    with _conn_lock, Session(_engine()) as s:
        devices = s.exec(
            select(DeviceRow).order_by(DeviceRow.device_order, DeviceRow.name)
        ).all()
        profiles = s.exec(
            select(DeviceProfileRow).order_by(
                DeviceProfileRow.device_name, DeviceProfileRow.profile_order, DeviceProfileRow.id
            )
        ).all()
        gamers = s.exec(
            select(DeviceProfileGamerRow).order_by(
                DeviceProfileGamerRow.profile_id,
                DeviceProfileGamerRow.gamer_order,
                DeviceProfileGamerRow.player_id,
            )
        ).all()
    return devices, profiles, gamers


def load_registry() -> Any:
    """Return a ``DeviceRegistry`` built from current SQLite rows.

    Local import of ``config.devices`` keeps this module free of a cyclic
    dependency — ``config.devices`` re-exports the dataclasses we hydrate here.
    """
    from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer
    from config.games import default_game as _default_game

    device_rows, profile_rows, gamer_rows = _load_registry_rows()

    profiles_by_device: dict[str, list[DeviceProfileRow]] = {}
    for p in profile_rows:
        profiles_by_device.setdefault(p.device_name, []).append(p)
    gamers_by_profile: dict[int, list[DeviceProfileGamerRow]] = {}
    for g in gamer_rows:
        gamers_by_profile.setdefault(g.profile_id, []).append(g)

    entries: list[DeviceEntry] = []
    for d in device_rows:
        device_game = d.game or _default_game()
        profile_entries: list[DeviceProfile] = []
        for prow in profiles_by_device.get(d.name, []):
            gamer_objs = tuple(
                Gamer(
                    id=int(g.player_id),
                    nickname=g.nickname or "",
                    level=int(g.level or 0),
                    game_package=g.game_package or "",
                )
                for g in gamers_by_profile.get(prow.id, [])
            )
            profile_game = prow.game or device_game
            profile_entries.append(
                DeviceProfile(
                    email=prow.email or "",
                    gamers=gamer_objs,
                    game=profile_game,
                )
            )

        crop: tuple[int, int, int, int] | None
        if all(c is not None for c in (d.quartz_crop_x, d.quartz_crop_y, d.quartz_crop_w, d.quartz_crop_h)):
            crop = (
                int(d.quartz_crop_x),
                int(d.quartz_crop_y),
                int(d.quartz_crop_w),
                int(d.quartz_crop_h),
            )
        else:
            crop = None

        entries.append(
            DeviceEntry(
                name=d.name,
                profiles=tuple(profile_entries),
                adb_serial=d.adb_serial or "",
                screenshot_backend=d.screenshot_backend or "",
                input_backend=d.input_backend or "",
                quartz_window_id=d.quartz_window_id,
                quartz_window_title=d.quartz_window_title or "",
                quartz_crop=crop,
                display=_deserialize_display(d.display_json),
                game=device_game,
            )
        )
    return DeviceRegistry(devices=entries)
