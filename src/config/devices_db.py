"""SQLite persistence for device + profile + gamer registry.

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

Shares the same SQLite file as ``state_sqlite`` / ``giftcodes_db`` (one
``state.db`` for all durable persistence).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from config.device_display import DeviceDisplayConfig
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_conn_lock = threading.RLock()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    name TEXT PRIMARY KEY,
    adb_serial TEXT NOT NULL DEFAULT '',
    screenshot_backend TEXT NOT NULL DEFAULT '',
    input_backend TEXT NOT NULL DEFAULT '',
    quartz_window_id INTEGER,
    quartz_window_title TEXT NOT NULL DEFAULT '',
    quartz_crop_x INTEGER,
    quartz_crop_y INTEGER,
    quartz_crop_w INTEGER,
    quartz_crop_h INTEGER,
    display_json TEXT,
    device_order INTEGER NOT NULL DEFAULT 0,
    game TEXT NOT NULL DEFAULT 'wos',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS device_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL REFERENCES devices(name) ON DELETE CASCADE,
    email TEXT NOT NULL DEFAULT '',
    profile_order INTEGER NOT NULL DEFAULT 0,
    game TEXT NOT NULL DEFAULT 'wos'
);

CREATE TABLE IF NOT EXISTS device_profile_gamers (
    profile_id INTEGER NOT NULL REFERENCES device_profiles(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 0,
    gamer_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_device_profiles_device ON device_profiles(device_name, profile_order);
CREATE INDEX IF NOT EXISTS idx_device_profile_gamers_profile ON device_profile_gamers(profile_id, gamer_order);
"""


VALID_SCREENSHOT_BACKENDS = frozenset({"", "quartz", "adb", "scrcpy"})
VALID_INPUT_BACKENDS = frozenset({"", "adb", "scrcpy"})


def _ensure_game_columns(conn: sqlite3.Connection) -> None:
    """One-time migration: add ``game`` column to legacy DB rows.

    Existing deployments created before Phase 2 lack the column. SQLite can't
    add NOT NULL columns with a literal default in one step on older DBs, so
    we detect and ALTER if missing. Backfills to ``'wos'`` since that was the
    only game until this phase.
    """
    for table in ("devices", "device_profiles"):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "game" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN game TEXT NOT NULL DEFAULT 'wos'")


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA_SQL)
        _ensure_game_columns(conn)
        # Minicap was removed in favor of scrcpy; keep old DB rows bootable.
        conn.execute(
            "UPDATE devices SET screenshot_backend = 'scrcpy' WHERE screenshot_backend = 'minicap'"
        )
        # Minitouch was removed; scrcpy is the replacement fast input backend.
        conn.execute(
            "UPDATE devices SET input_backend = 'scrcpy' WHERE input_backend = 'minitouch'"
        )
        conn.commit()
        yield conn
    finally:
        conn.close()


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
    with _conn_lock, _connect() as conn:
        # When ``game`` is omitted, keep whatever the existing row had (or fall
        # back to default_game() for new rows).
        if game is None:
            row = conn.execute(
                "SELECT game FROM devices WHERE name = ?", (name,)
            ).fetchone()
            if row is not None:
                game_value = row["game"]
        conn.execute(
            "INSERT INTO devices "
            "(name, adb_serial, screenshot_backend, input_backend, "
            "quartz_window_id, quartz_window_title, "
            "quartz_crop_x, quartz_crop_y, quartz_crop_w, quartz_crop_h, "
            "display_json, device_order, game, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "adb_serial = excluded.adb_serial, "
            "screenshot_backend = excluded.screenshot_backend, "
            "input_backend = excluded.input_backend, "
            "quartz_window_id = excluded.quartz_window_id, "
            "quartz_window_title = excluded.quartz_window_title, "
            "quartz_crop_x = excluded.quartz_crop_x, "
            "quartz_crop_y = excluded.quartz_crop_y, "
            "quartz_crop_w = excluded.quartz_crop_w, "
            "quartz_crop_h = excluded.quartz_crop_h, "
            "display_json = excluded.display_json, "
            "device_order = excluded.device_order, "
            "game = excluded.game, "
            "updated_at = excluded.updated_at",
            (
                name, adb_serial.strip(),
                screenshot_backend_clean,
                input_backend_clean,
                quartz_window_id, quartz_window_title.strip(),
                crop[0], crop[1], crop[2], crop[3],
                _serialize_display(display), device_order, game_value, now,
            ),
        )
        conn.commit()


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
    with _conn_lock, _connect() as conn:
        row = conn.execute("SELECT 1 FROM devices WHERE name = ?", (name,)).fetchone()
        if row is None:
            msg = f"device not found: {name!r}"
            raise KeyError(msg)
        conn.execute(
            "UPDATE devices SET game = ?, updated_at = ? WHERE name = ?",
            (game, time.time(), name),
        )
        conn.commit()
    return game


def set_profile_game(profile_id: int, game: str) -> str:
    """Override the per-profile game (defaults to the device's game otherwise)."""
    from config.games import is_known_game

    if not is_known_game(game):
        msg = f"unknown game id: {game!r}"
        raise ValueError(msg)
    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM device_profiles WHERE id = ?", (int(profile_id),)
        ).fetchone()
        if row is None:
            msg = f"profile not found: {profile_id!r}"
            raise KeyError(msg)
        conn.execute(
            "UPDATE device_profiles SET game = ? WHERE id = ?",
            (game, int(profile_id)),
        )
        conn.commit()
    return game


def delete_device(name: str) -> bool:
    """Delete a device row (cascades to profiles + gamers). Returns True iff a row was removed."""
    with _conn_lock, _connect() as conn:
        cur = conn.execute("DELETE FROM devices WHERE name = ?", ((name or "").strip(),))
        conn.commit()
        return cur.rowcount > 0


def device_exists(name: str) -> bool:
    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM devices WHERE name = ?", ((name or "").strip(),)
        ).fetchone()
    return row is not None


def count_devices() -> int:
    with _conn_lock, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()
    return int(row["n"] or 0)


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

    with _conn_lock, _connect() as conn:
        row = conn.execute(
            "SELECT screenshot_backend, input_backend FROM devices WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            msg = f"device not found: {name!r}"
            raise KeyError(msg)
        new_screenshot = screenshot_backend if screenshot_backend is not None else row["screenshot_backend"]
        new_input = input_backend if input_backend is not None else row["input_backend"]
        conn.execute(
            "UPDATE devices SET screenshot_backend = ?, input_backend = ?, updated_at = ? WHERE name = ?",
            (new_screenshot, new_input, time.time(), name),
        )
        conn.commit()
    return new_screenshot, new_input


# ---------------------------------------------------------------------------
# profile + gamer mutation (the path DSL `fetch_player` exercises)
# ---------------------------------------------------------------------------


def _find_device_row_id(
    conn: sqlite3.Connection, device_name_or_serial: str
) -> str | None:
    """Match on either name or adb_serial — callers may pass whichever they have."""
    candidate = device_name_or_serial.strip()
    if not candidate:
        return None
    row = conn.execute(
        "SELECT name FROM devices WHERE name = ? OR adb_serial = ? LIMIT 1",
        (candidate, candidate),
    ).fetchone()
    return row["name"] if row else None


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
    with _conn_lock, _connect() as conn:
        canonical = _find_device_row_id(conn, device_name)
        if canonical is None:
            conn.execute(
                "INSERT INTO devices (name, updated_at) VALUES (?, ?)",
                (device_name, now),
            )
            canonical = device_name
            changed = True

        profile_row = conn.execute(
            "SELECT id FROM device_profiles WHERE device_name = ? "
            "ORDER BY profile_order, id LIMIT 1",
            (canonical,),
        ).fetchone()
        if profile_row is None:
            # New profile inherits the device's game so the registry stays
            # internally consistent without forcing every caller to know it.
            device_game_row = conn.execute(
                "SELECT game FROM devices WHERE name = ?", (canonical,)
            ).fetchone()
            device_game = (device_game_row["game"] if device_game_row else "wos") or "wos"
            cur = conn.execute(
                "INSERT INTO device_profiles (device_name, email, profile_order, game) "
                "VALUES (?, ?, 0, ?)",
                (canonical, email, device_game),
            )
            profile_id = cur.lastrowid
            changed = True
        else:
            profile_id = profile_row["id"]

        gamer_row = conn.execute(
            "SELECT nickname FROM device_profile_gamers WHERE profile_id = ? AND player_id = ?",
            (profile_id, pid_int),
        ).fetchone()
        if gamer_row is None:
            # Append at the end — preserve insertion order.
            next_order_row = conn.execute(
                "SELECT COALESCE(MAX(gamer_order), -1) + 1 AS next FROM device_profile_gamers "
                "WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            next_order = int(next_order_row["next"] or 0)
            conn.execute(
                "INSERT INTO device_profile_gamers "
                "(profile_id, player_id, nickname, level, gamer_order) "
                "VALUES (?, ?, ?, 0, ?)",
                (profile_id, pid_int, nickname or "", next_order),
            )
            changed = True
        else:
            current_nick = gamer_row["nickname"] or ""
            if nickname and nickname != current_nick:
                conn.execute(
                    "UPDATE device_profile_gamers SET nickname = ? "
                    "WHERE profile_id = ? AND player_id = ?",
                    (nickname, profile_id, pid_int),
                )
                changed = True

        if changed:
            conn.execute(
                "UPDATE devices SET updated_at = ? WHERE name = ?",
                (now, canonical),
            )
        conn.commit()
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
    with _conn_lock, _connect() as conn:
        affected_devices = [
            row["device_name"]
            for row in conn.execute(
                "SELECT DISTINCT dp.device_name AS device_name "
                "FROM device_profile_gamers g "
                "JOIN device_profiles dp ON dp.id = g.profile_id "
                "WHERE g.player_id = ?",
                (pid_int,),
            ).fetchall()
        ]
        cur = conn.execute(
            "DELETE FROM device_profile_gamers WHERE player_id = ?",
            (pid_int,),
        )
        deleted = int(cur.rowcount or 0)
        if affected_devices:
            conn.executemany(
                "UPDATE devices SET updated_at = ? WHERE name = ?",
                [(now, name) for name in affected_devices],
            )
        conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# bulk load → DeviceRegistry
# ---------------------------------------------------------------------------


def _load_registry_rows() -> tuple[
    list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]
]:
    with _conn_lock, _connect() as conn:
        devices = conn.execute(
            "SELECT * FROM devices ORDER BY device_order, name"
        ).fetchall()
        profiles = conn.execute(
            "SELECT * FROM device_profiles ORDER BY device_name, profile_order, id"
        ).fetchall()
        gamers = conn.execute(
            "SELECT * FROM device_profile_gamers ORDER BY profile_id, gamer_order, player_id"
        ).fetchall()
    return devices, profiles, gamers


def load_registry() -> Any:
    """Return a ``DeviceRegistry`` built from current SQLite rows.

    Local import of ``config.devices`` keeps this module free of a cyclic
    dependency — ``config.devices`` re-exports the dataclasses we hydrate here.
    """
    from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer

    device_rows, profile_rows, gamer_rows = _load_registry_rows()

    profiles_by_device: dict[str, list[tuple[int, sqlite3.Row]]] = {}
    for p in profile_rows:
        profiles_by_device.setdefault(p["device_name"], []).append((p["id"], p))
    gamers_by_profile: dict[int, list[sqlite3.Row]] = {}
    for g in gamer_rows:
        gamers_by_profile.setdefault(g["profile_id"], []).append(g)

    from config.games import default_game as _default_game

    entries: list[DeviceEntry] = []
    for d in device_rows:
        profile_entries: list[DeviceProfile] = []
        d_keys = d.keys()
        device_game = (d["game"] if "game" in d_keys else _default_game()) or _default_game()
        for profile_id, prow in profiles_by_device.get(d["name"], []):
            gamer_objs = tuple(
                Gamer(
                    id=int(g["player_id"]),
                    nickname=g["nickname"] or "",
                    level=int(g["level"] or 0),
                )
                for g in gamers_by_profile.get(profile_id, [])
            )
            prow_keys = prow.keys()
            profile_game = (
                prow["game"] if "game" in prow_keys else device_game
            ) or device_game
            profile_entries.append(
                DeviceProfile(
                    email=prow["email"] or "",
                    gamers=gamer_objs,
                    game=profile_game,
                )
            )

        crop: tuple[int, int, int, int] | None
        if all(d[c] is not None for c in ("quartz_crop_x", "quartz_crop_y", "quartz_crop_w", "quartz_crop_h")):
            crop = (
                int(d["quartz_crop_x"]),
                int(d["quartz_crop_y"]),
                int(d["quartz_crop_w"]),
                int(d["quartz_crop_h"]),
            )
        else:
            crop = None

        entries.append(
            DeviceEntry(
                name=d["name"],
                profiles=tuple(profile_entries),
                adb_serial=d["adb_serial"] or "",
                screenshot_backend=d["screenshot_backend"] or "",
                input_backend=d["input_backend"] or "",
                quartz_window_id=d["quartz_window_id"],
                quartz_window_title=d["quartz_window_title"] or "",
                quartz_crop=crop,
                display=_deserialize_display(d["display_json"]),
                game=device_game,
            )
        )
    return DeviceRegistry(devices=entries)
