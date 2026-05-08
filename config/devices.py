"""Device registry loader — source of truth for BlueStacks instances and players.

Format mirrors db/devices.yaml:

    devices:
      - name: RF8RC00M8MF          # BlueStacks serial / ADB device name
        profiles:
          - email: user@gmail.com  # Google account
            gamer:
              - id: 123456789
                nickname: Hero1
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gamer:
    id: int
    nickname: str

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


def load_devices(path: Path | None = None) -> DeviceRegistry:
    if path is None:
        path = Path(__file__).parent.parent / "db" / "devices.yaml"
    if not path.exists():
        path = path.parent / "devices.example.yaml"

    raw = yaml.safe_load(path.read_text())
    devices: list[DeviceEntry] = []
    for d in raw.get("devices", []):
        profiles: list[DeviceProfile] = []
        for p in d.get("profiles", []):
            gamers = tuple(
                Gamer(id=int(g["id"]), nickname=g["nickname"])
                for g in p.get("gamer", [])
            )
            profiles.append(DeviceProfile(email=p["email"], gamers=gamers))
        devices.append(DeviceEntry(name=d["name"], profiles=tuple(profiles)))
    return DeviceRegistry(devices=devices)


def _load_devices_raw(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"devices": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {"devices": []}


def _save_devices_raw(path: Path, raw: dict[str, object]) -> None:
    try:
        content = yaml.dump(raw, allow_unicode=True, sort_keys=False)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        os.replace(tmp, path)
    except Exception:
        logger.exception("Failed to persist devices to %s", path)


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
    - Returns True when YAML was modified.
    """
    device_name = (device_name or "").strip()
    player_id = (player_id or "").strip()
    if not device_name or not player_id:
        return False

    raw = _load_devices_raw(path)
    devices = raw.get("devices")
    if not isinstance(devices, list):
        devices = []
        raw["devices"] = devices

    # Find or create device entry
    device: dict[str, object] | None = None
    for d in devices:
        if isinstance(d, dict) and str(d.get("name") or "").strip() == device_name:
            device = d
            break
    if device is None:
        device = {"name": device_name, "profiles": []}
        devices.append(device)

    profiles = device.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
        device["profiles"] = profiles

    if not profiles:
        profiles.append({"email": email, "gamer": []})

    profile0 = profiles[0]
    if not isinstance(profile0, dict):
        profile0 = {"email": email, "gamer": []}
        profiles[0] = profile0

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
                _save_devices_raw(path, raw)
                return True
            return False

    gamers.append({"id": int(player_id), "nickname": nickname or ""})
    _save_devices_raw(path, raw)
    return True


_registry: DeviceRegistry | None = None
_registry_lock = threading.Lock()


def get_device_registry() -> DeviceRegistry:
    global _registry  # noqa: PLW0603
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_devices()
    return _registry
