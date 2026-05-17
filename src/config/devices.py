"""Device registry loader — source of truth for BlueStacks instances and players.

Format mirrors db/devices.yaml:

    devices:
      - name: RF8RC00M8MF          # BlueStacks serial / ADB device name
        profiles:
          - email: user@gmail.com  # Google account
            gamer:
              - id: 123456789
                nickname: Hero1
                level: 15   # optional; used by scenario condition player_level_min
"""
from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

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
    if path is None:
        from config.paths import repo_root

        path = repo_root() / "db" / "devices.yaml"
    if not path.exists():
        path = path.parent / "devices.example.yaml"

    raw = yaml.safe_load(path.read_text())
    devices: list[DeviceEntry] = []
    for d in raw.get("devices", []):
        profiles: list[DeviceProfile] = []
        for p in d.get("profiles", []):
            gamers = tuple(
                Gamer(
                    id=int(g["id"]),
                    nickname=g["nickname"],
                    level=int(g.get("level", 0)),
                )
                for g in p.get("gamer", [])
            )
            profiles.append(DeviceProfile(email=p["email"], gamers=gamers))
        devices.append(
            DeviceEntry(
                name=d["name"],
                profiles=tuple(profiles),
                adb_serial=str(d.get("adb_serial") or "").strip(),
            )
        )
    return DeviceRegistry(devices=devices)


def _load_devices_raw(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"devices": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {"devices": []}


def _save_devices_raw(path: Path, raw: dict[str, object]) -> bool:
    """Atomically write *raw* to *path*.

    Returns True on successful persist, False if the write failed. Failure is
    logged but not raised, because callers still need to decide whether to
    surface an error to the user vs. fall through to a degraded path.
    """
    try:
        content = yaml.dump(raw, allow_unicode=True, sort_keys=False)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        Path(tmp).replace(path)
        return True
    except Exception:
        logger.exception("Failed to persist devices to %s", path)
        return False


def upsert_device_gamer(
    *,
    path: Path,
    device_name: str,
    player_id: str,
    nickname: str,
    email: str = "",
) -> bool:
    """Ensure player exists under device profiles.

    - Creates device if missing.
    - Uses first profile, or creates one with provided email (may be empty).
    - Returns True when YAML was modified **and** persisted.
    - Returns False when nothing changed OR persistence failed.

    The whole load-modify-save cycle runs under ``_devices_file_lock`` so two
    concurrent upserts can't lose each other's writes via last-writer-wins.
    """
    device_name = (device_name or "").strip()
    player_id = (player_id or "").strip()
    if not device_name or not player_id:
        return False

    with _devices_file_lock:
        raw = _load_devices_raw(path)
        devices_raw = raw.get("devices")
        if not isinstance(devices_raw, list):
            devices_raw = []
            raw["devices"] = devices_raw
        devices: list[Any] = cast("list[Any]", devices_raw)

        # Match by friendly name OR ADB serial — callers may pass either form
        # (UI passes `bs1`; runtime sometimes only has `127.0.0.1:5555`).
        # Without alias matching, the same physical device would get a second
        # entry under a different key and player→device lookups would split.
        device: dict[str, Any] | None = None
        for d_raw in devices:
            if not isinstance(d_raw, dict):
                continue
            d = cast("dict[str, Any]", d_raw)
            name = str(d.get("name") or "").strip()
            serial = str(d.get("adb_serial") or "").strip()
            if device_name in (name, serial) and (name or serial):
                device = d
                break
        if device is None:
            device = {"name": device_name, "profiles": []}
            devices.append(device)

        profiles_raw = device.get("profiles")
        if not isinstance(profiles_raw, list):
            profiles_raw = []
            device["profiles"] = profiles_raw
        profiles: list[Any] = cast("list[Any]", profiles_raw)

        if not profiles:
            profiles.append({"email": email, "gamer": []})

        profile0_raw = profiles[0]
        if not isinstance(profile0_raw, dict):
            profile0_raw = {"email": email, "gamer": []}
            profiles[0] = profile0_raw
        profile0: dict[str, Any] = cast("dict[str, Any]", profile0_raw)

        gamers = profile0.get("gamer")
        if not isinstance(gamers, list):
            gamers = []
            profile0["gamer"] = gamers

        # Update existing gamer or append new
        for g in gamers:
            if isinstance(g, dict) and str(g.get("id") or "").strip() == player_id:
                old_nick = str(g.get("nickname") or "")
                if nickname and nickname != old_nick:
                    g["nickname"] = nickname
                    if not _save_devices_raw(path, raw):
                        return False
                    _invalidate()
                    return True
                return False

        gamers.append({"id": int(player_id), "nickname": nickname or ""})
        if not _save_devices_raw(path, raw):
            return False
        _invalidate()
        return True


# ---------------------------------------------------------------------------
# Global registry cache
# ---------------------------------------------------------------------------

_registry: DeviceRegistry | None = None
_registry_lock = threading.Lock()
# Serializes the full ``load → mutate → save`` cycle in ``upsert_device_gamer``
# so two concurrent upserts can't last-writer-win each other (UI button + DSL
# fetch_player can race on the same ``db/devices.yaml``).
_devices_file_lock = threading.Lock()


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
    ``db/devices.yaml`` is often keyed by ``instance_id`` (for example ``bs1``).
    Accept both without forcing those names to be identical.
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
