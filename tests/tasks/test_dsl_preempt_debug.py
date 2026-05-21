"""Cooperative preemption when Streamlit debug UI enqueues Run scenario now."""

from __future__ import annotations

import pytest

from dashboard.redis_client import dsl_preempt_gen_key
from tasks.dsl_scenario import DslScenarioTask


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preempt_generation_detects_incr_after_snapshot(redis_async: object) -> None:
    inst = "bs1"
    key = dsl_preempt_gen_key(inst)
    t = DslScenarioTask(
        task_id="t1",
        player_id="p",
        priority=1,
        redis_client=redis_async,
        scenario_key="fake",
    )
    snap = await t._read_dsl_preempt_gen(inst)
    assert snap == 0
    t._preempt_gen_at_start = snap
    assert await t._preempted_by_new_debug(inst) is False

    await redis_async.incr(key)  # type: ignore[union-attr]  # ty: ignore[unresolved-attribute]
    assert await t._preempted_by_new_debug(inst) is True
