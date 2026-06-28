"""Shared planner-``owned`` persistence: Redis mirror + read-modify-write SQLite."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from games.wos.core.readers import persist as P


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hget(self, key: str, field: str) -> str | None:
        return self.store.get(key, {}).get(field)

    async def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.store.setdefault(key, {}).update(mapping)


class _FakeStore:
    """Minimal GamerStateStore stand-in: snapshot().planner + set('planner', …)."""

    def __init__(self, planner: dict[str, Any] | None = None) -> None:
        self._planner = copy.deepcopy(planner or {})

    def snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(planner=copy.deepcopy(self._planner))

    def set(self, key: str, value: Any) -> None:
        assert key == "planner"
        self._planner = copy.deepcopy(value)


class _FakeStateStore:
    def __init__(self, seed: dict[str, _FakeStore] | None = None) -> None:
        self.stores: dict[str, _FakeStore] = dict(seed or {})

    def get_or_create(self, player_id: str, nickname: str = "") -> _FakeStore:
        return self.stores.setdefault(str(player_id), _FakeStore())

    def get(self, player_id: str) -> _FakeStore | None:
        return self.stores.get(str(player_id))


PLAYER = "401227964"
PKEY = f"wos:player:{PLAYER}:state"
IKEY = "wos:instance:bs1:state"


@pytest.fixture
def state_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStateStore:
    store = _FakeStateStore()
    monkeypatch.setattr("config.state_store.get_state_store", lambda: store)
    return store


# --------------------------------------------------------------------------- #
# persist_planner_owned
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_persist_mirrors_to_both_hashes_and_sqlite(state_store: _FakeStateStore) -> None:
    redis = _FakeRedis()
    owned = {"snow_leopard": {"level": 30, "refine": 5, "skill": 3}}

    ok = await P.persist_planner_owned(
        redis, player_id=PLAYER, instance_id="bs1", domain="pets", owned=owned
    )
    assert ok is True

    # Hot mirror: the field name MUST equal the observed_input ("pets.owned") so
    # `botctl planners` clears blind. Written to BOTH hashes.
    for key in (PKEY, IKEY):
        assert json.loads(redis.store[key]["pets.owned"]) == owned
        assert "pets.owned.synced_at" in redis.store[key]

    # Durable SQLite: the nested owned dict actually landed (read-modify-write).
    snap = state_store.get(PLAYER).snapshot()
    assert snap.planner["pets"]["owned"] == owned


@pytest.mark.asyncio
async def test_persist_creates_nested_owned_from_empty_planner(state_store: _FakeStateStore) -> None:
    """Regression for the _set_nested trap: writing into an EMPTY planner dict must
    create planner['<domain>']['owned'] — update_from_flat would silently no-op."""
    redis = _FakeRedis()
    owned = {"infantry_1": 5, "marksman_6": 2}

    await P.persist_planner_owned(
        redis, player_id=PLAYER, instance_id="bs1", domain="charms", owned=owned
    )

    snap = state_store.get(PLAYER).snapshot()
    assert snap.planner["charms"]["owned"] == owned  # not lost


@pytest.mark.asyncio
async def test_persist_preserves_other_keys_in_domain(state_store: _FakeStateStore) -> None:
    # Operator already set a target on the pets domain — the reader must not clobber it.
    state_store.stores[PLAYER] = _FakeStore({"pets": {"target_levels": {"snow_leopard": 40}}})
    redis = _FakeRedis()

    await P.persist_planner_owned(
        redis, player_id=PLAYER, instance_id="bs1", domain="pets",
        owned={"snow_leopard": {"level": 30}},
    )

    planner = state_store.get(PLAYER).snapshot().planner
    assert planner["pets"]["owned"] == {"snow_leopard": {"level": 30}}
    assert planner["pets"]["target_levels"] == {"snow_leopard": 40}  # preserved


@pytest.mark.asyncio
async def test_persist_extra_flat_for_island(state_store: _FakeStateStore) -> None:
    redis = _FakeRedis()
    owned = {"tree_of_life_level": 5, "prosperity": 1200}

    await P.persist_planner_owned(
        redis, player_id=PLAYER, instance_id="bs1", domain="island", owned=owned,
        extra_flat={"island.tree_of_life.level": 5},
    )

    # island's observed_input is the flat key, not <domain>.owned.
    assert redis.store[PKEY]["island.tree_of_life.level"] == "5"
    assert redis.store[IKEY]["island.tree_of_life.level"] == "5"
    assert json.loads(redis.store[PKEY]["island.owned"]) == owned


@pytest.mark.asyncio
async def test_persist_skips_on_bad_input(state_store: _FakeStateStore) -> None:
    redis = _FakeRedis()
    assert await P.persist_planner_owned(
        redis, player_id="", instance_id="bs1", domain="pets", owned={"x": 1}
    ) is False
    assert await P.persist_planner_owned(
        None, player_id=PLAYER, instance_id="bs1", domain="pets", owned={"x": 1}
    ) is False
    assert redis.store == {}


# --------------------------------------------------------------------------- #
# overlay_durable_planner_owned
# --------------------------------------------------------------------------- #
def test_overlay_backfills_cold_mirror(state_store: _FakeStateStore) -> None:
    state_store.stores[PLAYER] = _FakeStore({
        "pets": {"owned": {"snow_leopard": {"level": 30}}},
        "island": {"owned": {"tree_of_life_level": 7}},
    })
    state: dict[str, Any] = {}  # cold mirror after a flush

    P.overlay_durable_planner_owned(PLAYER, state)

    assert json.loads(state["pets.owned"]) == {"snow_leopard": {"level": 30}}
    assert state["island.tree_of_life.level"] == "7"


def test_overlay_is_fill_only(state_store: _FakeStateStore) -> None:
    state_store.stores[PLAYER] = _FakeStore({"pets": {"owned": {"snow_leopard": {"level": 30}}}})
    state = {"pets.owned": "LIVE"}  # a live reading already present

    P.overlay_durable_planner_owned(PLAYER, state)

    assert state["pets.owned"] == "LIVE"  # never overwritten


def test_overlay_noop_when_no_durable_data(state_store: _FakeStateStore) -> None:
    state_store.stores[PLAYER] = _FakeStore({})
    state: dict[str, Any] = {}
    P.overlay_durable_planner_owned(PLAYER, state)
    assert state == {}
