from __future__ import annotations

import pytest

from config.state_sqlite import (
    get_alliance_stats,
    set_state_db_path_for_tests,
)
from tasks.dsl_exec import DslExecContext
from tasks.dsl_exec.registry import build_dsl_exec_registry


class _FakeRedis:
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields

    async def hget(self, key: str, field: str) -> str | None:
        if key != "wos:instance:bs1:state":
            return None
        return self.fields.get(field)


class _FakeGamerStore:
    def __init__(self) -> None:
        self.flat: dict[str, object] = {}

    def update_from_flat(self, mapping: dict[str, object]) -> None:
        self.flat.update(mapping)


class _FakeStateStore:
    def __init__(self) -> None:
        self.players: dict[str, _FakeGamerStore] = {}

    def get_or_create(self, player_id: str, nickname: str = "") -> _FakeGamerStore:
        return self.players.setdefault(player_id, _FakeGamerStore())


@pytest.mark.asyncio
async def test_sync_alliance_stats_parses_ocr_values(tmp_path) -> None:
    registry = build_dsl_exec_registry()
    set_state_db_path_for_tests(tmp_path / "state.db")
    try:
        ctx = DslExecContext(
            redis_client=_FakeRedis(
                {
                    "alliance.name": "KINGLACUNI",
                    "alliance.power": "4,388,228,831",
                    "alliance.rank": "3",
                    "alliance.members.count": "81/88",
                    "alliance.level.badge": "10",
                }
            ),
            player_id="",
            instance_id="bs1",
            args={},
        )

        await registry["sync_alliance_stats"](ctx)

        assert ctx.result["action"] == "stored"
        assert ctx.result["power"] == 4_388_228_831
        assert ctx.result["rank"] == 3
        assert ctx.result["level"] == 10
        assert ctx.result["members_count"] == 81
        assert ctx.result["members_max"] == 88

        stats = get_alliance_stats("KINGLACUNI")
        assert stats["series"] == [
            {
                "day": stats["series"][0]["day"],
                "power": 4_388_228_831,
                "rank": 3,
                "level": 10,
                "members_count": 81,
                "members_max": 88,
            }
        ]
    finally:
        set_state_db_path_for_tests(None)


@pytest.mark.asyncio
async def test_sync_alliance_stats_mirrors_name_to_player_state(tmp_path, monkeypatch) -> None:
    """With an active player, the overview name is mirrored to durable player state.

    scan_alliance_members' _resolve_alliance_name prefers this player-scoped
    value, so the daily cron can resolve the alliance even without a fresh
    overview visit.
    """
    registry = build_dsl_exec_registry()
    set_state_db_path_for_tests(tmp_path / "state.db")
    fake_store = _FakeStateStore()
    handler = registry["sync_alliance_stats"]
    # The exec module is loaded under a synthesized name, so patch the name in
    # the handler's own globals (the namespace it resolves get_state_store from).
    monkeypatch.setitem(handler.__globals__, "get_state_store", lambda: fake_store)
    try:
        ctx = DslExecContext(
            redis_client=_FakeRedis(
                {
                    "alliance.name": "[VAL]VictoryAndLegacy",
                    "alliance.power": "4,914,745,721",
                    "alliance.rank": "1",
                    "alliance.members.count": "83/88",
                    "alliance.level.badge": "11",
                }
            ),
            player_id="3295843",
            instance_id="bs1",
            args={},
        )

        await handler(ctx)

        assert ctx.result["action"] == "stored"
        assert "3295843" in fake_store.players
        assert fake_store.players["3295843"].flat == {"alliance.name": "[VAL]VictoryAndLegacy"}
    finally:
        set_state_db_path_for_tests(None)


@pytest.mark.asyncio
async def test_sync_alliance_stats_rejects_search_placeholder(tmp_path, monkeypatch) -> None:
    """OCR'ing the member-search box ("Chief ID or name") must not persist anything.

    This is the wrong-screen guard: screen detection occasionally lands on
    alliance.members, whose search box sits where the overview name is.
    """
    registry = build_dsl_exec_registry()
    set_state_db_path_for_tests(tmp_path / "state.db")
    fake_store = _FakeStateStore()
    handler = registry["sync_alliance_stats"]
    monkeypatch.setitem(handler.__globals__, "get_state_store", lambda: fake_store)
    try:
        ctx = DslExecContext(
            redis_client=_FakeRedis(
                {
                    "alliance.name": "Chief ID or name",
                    "alliance.members.count": "33/88",
                }
            ),
            player_id="3295843",
            instance_id="bs1",
            args={},
        )

        await handler(ctx)

        assert ctx.result["reason"] == "search_placeholder"
        assert "action" not in ctx.result
        # No alliance row and no player-state write under the bogus name.
        assert get_alliance_stats("Chief ID or name")["series"] == []
        assert fake_store.players == {}
    finally:
        set_state_db_path_for_tests(None)


@pytest.mark.asyncio
async def test_sync_alliance_stats_requires_name(tmp_path) -> None:
    registry = build_dsl_exec_registry()
    set_state_db_path_for_tests(tmp_path / "state.db")
    try:
        ctx = DslExecContext(
            redis_client=_FakeRedis(
                {
                    "alliance.power": "4,388,228,831",
                    "alliance.rank": "3",
                    "alliance.members.count": "81/88",
                    "alliance.level.badge": "10",
                }
            ),
            player_id="",
            instance_id="bs1",
            args={},
        )

        await registry["sync_alliance_stats"](ctx)

        assert ctx.result == {"reason": "missing_alliance_name"}
        assert get_alliance_stats("KINGLACUNI")["series"] == []
    finally:
        set_state_db_path_for_tests(None)
