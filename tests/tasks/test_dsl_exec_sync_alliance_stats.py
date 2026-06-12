from __future__ import annotations

import pytest

from config.state_sqlite import (
    get_alliance_stats,
    set_state_db_path_for_tests,
)
from tasks.dsl_exec import DslExecContext
from tasks.dsl_exec.registry import build_dsl_exec_registry


def _licensed_registry(monkeypatch: pytest.MonkeyPatch, licensed: bool = True):
    registry = build_dsl_exec_registry()
    handler = registry["sync_alliance_stats"]
    monkeypatch.setitem(handler.__globals__, "has_feature", lambda _feature: licensed)
    return registry


class _FakeRedis:
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields

    async def hget(self, key: str, field: str) -> str | None:
        if key != "wos:instance:bs1:state":
            return None
        return self.fields.get(field)


@pytest.mark.asyncio
async def test_sync_alliance_stats_parses_ocr_values(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _licensed_registry(monkeypatch)
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
async def test_sync_alliance_stats_requires_name(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _licensed_registry(monkeypatch)
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


@pytest.mark.asyncio
async def test_sync_alliance_stats_requires_r4_feature(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _licensed_registry(monkeypatch, licensed=False)
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

        assert ctx.result == {
            "reason": "feature_not_licensed",
            "feature": "alliance_stats",
            "required_tier": "r4",
        }
        assert get_alliance_stats("KINGLACUNI")["series"] == []
    finally:
        set_state_db_path_for_tests(None)
