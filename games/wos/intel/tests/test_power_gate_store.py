"""Power-gate decision must be visible to DSL ``cond`` (state store, not Redis).

Regression for live bs4 2026-08-05: the exec wrote the decision only to the
Redis player hash, but ``cond: intel.power_gate == "fight"`` evaluates against
the SQLite state-store flat dict — so the fight branch never fired and the run
stranded the device on squad_settings with a staged, unfired squad.
"""
from __future__ import annotations

from typing import Any

import games.wos.intel.exec as EXEC
import pytest

from tasks.dsl_exec.context import DslExecContext


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update(mapping)
            return len(mapping)
        assert field is not None
        bucket[field] = value
        return 1


class _GateActions:
    """Capture fails → powers unreadable → decision defaults to ``fight``."""

    def capture_screen_bgr_adb(self, _instance_id: str) -> Any:
        msg = "no adb in test"
        raise RuntimeError(msg)

    def capture_screen_bgr(self, _instance_id: str) -> Any:
        return None

    def system_back(self, _instance_id: str) -> None:
        msg = "fight decision must not back out"
        raise AssertionError(msg)


@pytest.mark.asyncio
async def test_power_gate_decision_lands_in_state_store_for_cond(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import config.state_store as state_store_mod
    import services
    from config.state_sqlite import set_state_db_path_for_tests

    fresh = state_store_mod.StateStore(path=tmp_path / "state.db")
    monkeypatch.setattr(state_store_mod, "_global_store", fresh)
    monkeypatch.setattr(services, "get_ocr_client", lambda: None)
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: _GateActions())
    try:
        redis = _FakeRedis()
        ctx = DslExecContext(
            redis_client=redis, player_id="101", instance_id="i1", args={}
        )
        await EXEC._exec_intel_power_gate(ctx)

        assert ctx.result["action"] == "fight"
        # Redis keeps the dashboard/debug copy…
        assert redis.hashes["wos:player:101:state"]["intel.power_gate"] == "fight"
        # …and the state store carries the copy the DSL cond actually reads.
        from layout.area_versions import eval_cond

        flat = fresh.get_or_create("101").to_flat_dict()
        assert flat.get("intel.power_gate") == "fight"
        assert eval_cond('intel.power_gate == "fight"', flat) is True
        assert eval_cond('intel.power_gate == "flee"', flat) is False
    finally:
        set_state_db_path_for_tests(None)
