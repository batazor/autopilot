from __future__ import annotations

import time

import pytest

from analysis.overlay_ttl_state import (
    overlay_ttl_key,
    persist_overlay_ttl_state_to_redis,
    sync_overlay_ttl_state_from_redis,
)


@pytest.mark.asyncio
async def test_overlay_ttl_sync_clears_in_memory_state_when_redis_field_deleted(
    redis_async: object,
) -> None:
    r = redis_async
    key = overlay_ttl_key(instance_id="bs1", player_id="p1")
    await r.hset(key, mapping={"isWorkers.visible": f"{time.time():.3f}"})  # type: ignore[attr-defined]
    state: dict[str, float] = {}

    await sync_overlay_ttl_state_from_redis(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
    )
    assert "isWorkers.visible" in state

    await r.hdel(key, "isWorkers.visible")  # type: ignore[attr-defined]
    await sync_overlay_ttl_state_from_redis(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
    )

    assert state == {}


@pytest.mark.asyncio
async def test_overlay_ttl_persist_writes_player_scoped_snapshot(
    redis_async: object,
) -> None:
    r = redis_async
    state = {"isWorkers.visible": time.monotonic()}

    await persist_overlay_ttl_state_to_redis(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
    )

    saved = await r.hget(  # type: ignore[attr-defined]
        overlay_ttl_key(instance_id="bs1", player_id="p1"),
        "isWorkers.visible",
    )
    assert saved is not None
    assert abs(float(saved) - time.time()) < 2.0
