"""Generic per-account options API: registry-driven list + validated set/persist."""
from __future__ import annotations

from typing import Any

import pytest
from games.wos.core.arena.opponent_filter import SETTING_KEY as ARENA_KEY

from api.routers import farm as farm_api


class _FakeGamer:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def to_flat_dict(self) -> dict[str, Any]:
        return dict(self.values)


class _FakeStore:
    def __init__(self, gamers: dict[str, _FakeGamer] | None = None) -> None:
        self.gamers = gamers or {}

    def get(self, pid: str) -> _FakeGamer | None:
        return self.gamers.get(str(pid))

    def get_or_create(self, pid: str, nickname: str = "") -> _FakeGamer:
        g = self.gamers.get(str(pid))
        if g is None:
            g = _FakeGamer()
            self.gamers[str(pid)] = g
        return g


def _account_with_fid(monkeypatch: Any, fid: str) -> None:
    monkeypatch.setattr(
        farm_api.farm_accounts_db,
        "get_account",
        lambda username, *, game: farm_api.farm_accounts_db.FarmAccount(
            game=game,
            username=username,
            characters=(
                farm_api.farm_accounts_db.FarmCharacter(
                    game=game, username=username, server="s1", fid=fid
                ),
            ),
        ),
    )


def _arena_row(result: dict[str, Any]) -> dict[str, Any]:
    return next(o for o in result["options"] if o["key"] == ARENA_KEY)


def test_get_options_lists_registry_with_current_value(monkeypatch: Any) -> None:
    store = _FakeStore({"222": _FakeGamer({ARENA_KEY: True})})
    monkeypatch.setattr(farm_api, "get_state_store", lambda: store)
    _account_with_fid(monkeypatch, "222")

    row = _arena_row(farm_api.get_character_options("mossvale", "222"))
    assert row["value"] is True
    assert row["type"] == "bool"


def test_get_options_defaults_when_unset(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    _account_with_fid(monkeypatch, "222")

    row = _arena_row(farm_api.get_character_options("mossvale", "222"))
    assert row["value"] is False    # default, nothing stored


def test_set_option_coerces_and_persists(monkeypatch: Any) -> None:
    store = _FakeStore()
    monkeypatch.setattr(farm_api, "get_state_store", lambda: store)
    _account_with_fid(monkeypatch, "222")

    result = farm_api.set_character_option(
        "mossvale", "222", farm_api.OptionBody(key=ARENA_KEY, value="true")
    )
    assert result == {"username": "mossvale", "fid": "222", "key": ARENA_KEY, "value": True}
    assert store.gamers["222"].get(ARENA_KEY) is True


def test_set_option_unknown_key_404(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    _account_with_fid(monkeypatch, "222")

    with pytest.raises(farm_api.HTTPException) as exc:
        farm_api.set_character_option(
            "mossvale", "222", farm_api.OptionBody(key="planner.nope", value=True)
        )
    assert exc.value.status_code == 404


def test_set_option_404_for_unknown_character(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    _account_with_fid(monkeypatch, "222")

    with pytest.raises(farm_api.HTTPException) as exc:
        farm_api.set_character_option(
            "mossvale", "999", farm_api.OptionBody(key=ARENA_KEY, value=True)
        )
    assert exc.value.status_code == 404
