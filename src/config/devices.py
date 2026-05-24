"""Device registry: BlueStacks instances + accounts + players.

Persistence lives in SQLite (``src/config/devices_db.py``); this module owns
the dataclass contract every caller uses and keeps a process-wide cache of the
hydrated ``DeviceRegistry``. Callers should continue to call ``load_devices``,
``upsert_device_gamer``, and ``invalidate_device_registry`` exactly as before.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from config.device_display import DeviceDisplayConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gamer:
    id: int
    nickname: str
    level: int = 0

    @property
    def player_id(self) -> str:
        return str(self.id)


@dataclass(frozen=True)
class DeviceProfile:
    email: str
    gamers: tuple[Gamer, ...]

    def player_ids(self) -> list[str]:
        return [str(g.id) for g in self.gamers]


@dataclass(frozen=True)
class DeviceEntry:
    name: str
    profiles: tuple[DeviceProfile, ...]
    adb_serial: str = ""
    screenshot_backend: str = ""
    input_backend: str = ""
    quartz_window_id: int | None = None
    quartz_window_title: str = ""
    quartz_crop: tuple[int, int, int, int] | None = None
    display: DeviceDisplayConfig | None = None

    @property
    def effective_serial(self) -> str:
        """ADB serial for this device — explicit ``adb_serial`` or fall back to ``name``."""
        return self.adb_serial.strip() or self.name.strip()

    def all_player_ids(self) -> list[str]:
        return [pid for p in self.profiles for pid in p.player_ids()]

    def all_gamers(self) -> list[Gamer]:
        return [g for p in self.profiles for g in p.gamers]


@dataclass
class DeviceRegistry:
    devices: list[DeviceEntry] = field(default_factory=list)

    def get_device_for_player(self, player_id: str) -> DeviceEntry | None:
        for device in self.devices:
            if player_id in device.all_player_ids():
                return device
        return None

    def get_gamer(self, player_id: str) -> Gamer | None:
        for device in self.devices:
            for gamer in device.all_gamers():
                if str(gamer.id) == player_id:
                    return gamer
        return None

    def all_player_ids(self) -> list[str]:
        return [pid for d in self.devices for pid in d.all_player_ids()]

    def player_ids_for_device(self, device_name: str) -> list[str]:
        """Player IDs registered under *device_name*.

        Matches either ``DeviceEntry.name`` (friendly alias like ``bs1``) or
        ``DeviceEntry.adb_serial`` / ``effective_serial`` (raw ADB serial like
        ``127.0.0.1:5555``) so callers can pass whichever form they have.
        """
        for d in self.devices:
            if d.name == device_name or d.effective_serial == device_name:
                return d.all_player_ids()
        return []


def load_devices(path: Path | None = None) -> DeviceRegistry:
    """Return the full device registry from SQLite.

    ``path`` is accepted only for backwards compatibility with callers that
    used to pass an explicit YAML file path — it's intentionally ignored.
    SQLite is the sole source of truth; one ``wos.db`` per repo.
    """
    from config.devices_db import load_registry

    return load_registry()


def upsert_device_gamer(
    *,
    device_name: str,
    player_id: str,
    nickname: str,
    email: str = "",
    path: Path | None = None,
) -> bool:
    """Ensure ``player_id`` exists under the device's first profile.

    Returns True iff something was changed. Invalidates the global registry
    cache so the next ``get_device_registry()`` reads the fresh row.
    """
    from config.devices_db import upsert_device_gamer as _db_upsert

    changed = _db_upsert(device_name, player_id, nickname, email=email)
    if changed:
        _invalidate()
    return changed


# ---------------------------------------------------------------------------
# Global registry cache
# ---------------------------------------------------------------------------

_registry: DeviceRegistry | None = None
_registry_lock = threading.Lock()


def invalidate_device_registry() -> None:
    _invalidate()


def _invalidate() -> None:
    global _registry
    with _registry_lock:
        _registry = None


def get_device_registry() -> DeviceRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_devices()
    return _registry


def player_ids_for_device(device_name: str) -> list[str]:
    """Convenience wrapper: player IDs for *device_name* from the global registry."""
    return get_device_registry().player_ids_for_device(device_name)


def player_ids_for_device_candidates(*device_names: str) -> list[str]:
    """Player IDs for the first matching device alias.

    Settings historically pass the ADB serial (``bluestacks_window_title``), while
    ``devices`` may be keyed by ``instance_id`` (for example ``bs1``). Accept
    both without forcing those names to be identical.
    """
    registry = get_device_registry()
    seen_names: set[str] = set()
    for raw in device_names:
        name = str(raw or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        players = registry.player_ids_for_device(name)
        if players:
            return players
    return []
