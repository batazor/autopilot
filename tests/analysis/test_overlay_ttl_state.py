from __future__ import annotations

import time

import pytest

from analysis.overlay_ttl_state import (
    bump_overlay_ttl_rev,
    maybe_persist_overlay_ttl_state_to_redis,
    overlay_ttl_key,
    persist_overlay_ttl_state_to_redis,
    sync_overlay_ttl_state_from_redis,
    sync_overlay_ttl_state_if_needed,
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
    await bump_overlay_ttl_rev(r, instance_id="bs1", player_id="p1")  # type: ignore[arg-type]
    rev, _ = await sync_overlay_ttl_state_if_needed(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
        cached_rev="0",
        last_sync_mono=0.0,
        force_interval_s=9999.0,
    )
    assert rev != "0"
    assert state == {}


@pytest.mark.asyncio
async def test_overlay_ttl_sync_skipped_when_rev_unchanged(redis_async: object) -> None:
    r = redis_async
    key = overlay_ttl_key(instance_id="bs1", player_id="p1")
    await r.hset(key, mapping={"rule.a": f"{time.time():.3f}"})  # type: ignore[attr-defined]
    state: dict[str, float] = {"stale": time.monotonic()}
    rev0 = "1"
    await r.set(f"{key}:rev", rev0)  # type: ignore[attr-defined]

    rev, _ = await sync_overlay_ttl_state_if_needed(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
        cached_rev=rev0,
        last_sync_mono=time.monotonic(),
        force_interval_s=9999.0,
    )

    assert rev == rev0
    assert "stale" in state
    assert "rule.a" not in state


@pytest.mark.asyncio
async def test_overlay_ttl_persist_throttled(redis_async: object) -> None:
    r = redis_async
    state = {"isWorkers.visible": time.monotonic()}

    t0 = await maybe_persist_overlay_ttl_state_to_redis(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
        last_persist_mono=None,
        min_interval_s=60.0,
    )
    assert t0 is not None
    saved1 = await r.hget(overlay_ttl_key(instance_id="bs1", player_id="p1"), "isWorkers.visible")  # type: ignore[attr-defined]
    assert saved1 is not None

    t1 = await maybe_persist_overlay_ttl_state_to_redis(  # type: ignore[arg-type]
        r,
        instance_id="bs1",
        player_id="p1",
        rule_eval_state=state,
        last_persist_mono=t0,
        min_interval_s=60.0,
    )
    assert t1 == t0


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
