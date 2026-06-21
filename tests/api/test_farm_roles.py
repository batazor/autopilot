"""Farm per-character role badge API: catalog, read-through, and set/persist."""
from __future__ import annotations

from typing import Any

import pytest

from api.routers import farm as farm_api


class _FakeGamer:
    """Minimal stand-in for a GamerStateStore: dot-key get/set over a dict."""

    def __init__(self, role: str | None = None) -> None:
        self.values: dict[str, Any] = {}
        if role is not None:
            self.values["planner.role"] = role

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value


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


def test_list_roles_returns_catalog(monkeypatch: Any) -> None:
    result = farm_api.list_roles()
    ids = {r["id"] for r in result["roles"]}
    assert ids == {"balanced", "farm", "fighter"}
    farm = next(r for r in result["roles"] if r["id"] == "farm")
    assert farm["label"] and farm["description"]          # human labels present


def test_character_role_defaults_to_balanced_when_unset(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    assert farm_api._character_role("222") == "balanced"   # no stored role → default
    assert farm_api._character_role("") == "balanced"      # no fid → default


def test_character_role_reads_stored_value(monkeypatch: Any) -> None:
    store = _FakeStore({"222": _FakeGamer(role="farm")})
    monkeypatch.setattr(farm_api, "get_state_store", lambda: store)
    assert farm_api._character_role("222") == "farm"


def test_set_character_role_persists(monkeypatch: Any) -> None:
    store = _FakeStore()
    monkeypatch.setattr(farm_api, "get_state_store", lambda: store)
    _account_with_fid(monkeypatch, "222")

    result = farm_api.post_character_role("mossvale", "222", farm_api.RoleBody(role="Farm"))

    assert result == {"username": "mossvale", "fid": "222", "role": "farm"}
    assert store.gamers["222"].get("planner.role") == "farm"  # normalised + stored


def test_set_character_role_rejects_unknown_role(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    _account_with_fid(monkeypatch, "222")

    with pytest.raises(farm_api.HTTPException) as exc:
        farm_api.post_character_role("mossvale", "222", farm_api.RoleBody(role="banana"))
    assert exc.value.status_code == 400


def test_set_character_role_404_for_unknown_character(monkeypatch: Any) -> None:
    monkeypatch.setattr(farm_api, "get_state_store", lambda: _FakeStore())
    _account_with_fid(monkeypatch, "222")

    with pytest.raises(farm_api.HTTPException) as exc:
        farm_api.post_character_role("mossvale", "999", farm_api.RoleBody(role="farm"))
    assert exc.value.status_code == 404
