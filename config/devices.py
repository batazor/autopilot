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

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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


_registry: DeviceRegistry | None = None


def get_device_registry() -> DeviceRegistry:
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = load_devices()
    return _registry
