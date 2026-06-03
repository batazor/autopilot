"""``all_player_ids(game=...)`` filtering on the device registry.

Regression for a ``TypeError: all_player_ids() got an unexpected keyword
argument 'game'`` that broke ``gift_codes_poll[kingshot]`` redemption.
"""
from __future__ import annotations

from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer


def _gamers(*ids: int) -> tuple[Gamer, ...]:
    return tuple(Gamer(id=i, nickname=f"P{i}") for i in ids)


def _registry() -> DeviceRegistry:
    # One device, two profiles on different games (plus a profile that inherits
    # the device default game).
    wos = DeviceProfile(email="a@x", gamers=_gamers(1, 2), game="wos")
    kingshot = DeviceProfile(email="b@x", gamers=_gamers(3), game="kingshot")
    return DeviceRegistry(
        devices=[DeviceEntry(name="bs1", profiles=(wos, kingshot), game="wos")]
    )


def test_no_game_returns_every_player() -> None:
    assert _registry().all_player_ids() == ["1", "2", "3"]


def test_filters_to_requested_game() -> None:
    reg = _registry()
    assert reg.all_player_ids(game="kingshot") == ["3"]
    assert reg.all_player_ids(game="wos") == ["1", "2"]


def test_unknown_game_yields_nothing() -> None:
    assert _registry().all_player_ids(game="whiteout2") == []


def test_profile_without_explicit_game_inherits_device_default() -> None:
    prof = DeviceProfile(email="c@x", gamers=_gamers(9), game="")
    reg = DeviceRegistry(devices=[DeviceEntry(name="bs1", profiles=(prof,), game="kingshot")])
    assert reg.all_player_ids(game="kingshot") == ["9"]
    assert reg.all_player_ids(game="wos") == []
