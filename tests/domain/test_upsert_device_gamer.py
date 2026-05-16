from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from config import devices as devices_mod
from config.devices import upsert_device_gamer


@pytest.fixture(autouse=True)
def _isolate_registry() -> Any:
    """Clear the cached registry before/after each test."""
    devices_mod._invalidate()
    yield
    devices_mod._invalidate()


def _write_devices(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_upsert_device_gamer_returns_false_on_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the YAML cannot be persisted, ``upsert_device_gamer`` must report
    failure rather than claim success.

    Previously the function returned True regardless of save outcome because
    ``_save_devices_raw`` swallowed exceptions. Callers (UI / DSL) used that
    boolean to message the user, so a silent save failure was indistinguishable
    from a successful link.
    """
    path = tmp_path / "devices.yaml"
    _write_devices(path, "devices: []\n")

    monkeypatch.setattr(
        devices_mod, "_save_devices_raw", lambda _p, _r: False
    )

    ok = upsert_device_gamer(
        path=path,
        device_name="bs1",
        player_id="123",
        nickname="hero",
    )
    assert ok is False


def test_upsert_device_gamer_matches_by_adb_serial(tmp_path: Path) -> None:
    """When the registry was seeded with ``adb_serial`` only, calling upsert
    with the serial as ``device_name`` must hit the existing entry rather than
    creating a duplicate row keyed by the serial."""
    path = tmp_path / "devices.yaml"
    _write_devices(
        path,
        yaml.dump(
            {
                "devices": [
                    {
                        "name": "bs1",
                        "adb_serial": "127.0.0.1:5555",
                        "profiles": [{"email": "", "gamer": []}],
                    }
                ]
            }
        ),
    )

    ok = upsert_device_gamer(
        path=path,
        device_name="127.0.0.1:5555",
        player_id="765502864",
        nickname="lord",
    )
    assert ok is True

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(raw["devices"]) == 1, raw["devices"]
    gamers = raw["devices"][0]["profiles"][0]["gamer"]
    assert gamers == [{"id": 765502864, "nickname": "lord"}]


def test_upsert_device_gamer_appends_new_device_when_no_match(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    _write_devices(path, "devices: []\n")

    ok = upsert_device_gamer(
        path=path,
        device_name="bs2",
        player_id="42",
        nickname="newcomer",
    )
    assert ok is True

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["devices"][0]["name"] == "bs2"
    assert raw["devices"][0]["profiles"][0]["gamer"] == [
        {"id": 42, "nickname": "newcomer"}
    ]


def test_upsert_device_gamer_noop_returns_false_when_nothing_changed(tmp_path: Path) -> None:
    """A repeat call with the same nickname is a no-op — must return False so
    callers don't trigger a spurious 'linked' toast."""
    path = tmp_path / "devices.yaml"
    _write_devices(path, "devices: []\n")

    assert upsert_device_gamer(
        path=path,
        device_name="bs1",
        player_id="1",
        nickname="hero",
    ) is True
    # Same call again — nothing to do.
    assert upsert_device_gamer(
        path=path,
        device_name="bs1",
        player_id="1",
        nickname="hero",
    ) is False
