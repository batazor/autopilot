"""Intel started-event memory: don't re-start a pin whose march is still out.

Two layers under test:

* :mod:`games.wos.intel.started` — the pure filter + the TTL-bounded Redis store
  that remembers which pins were started (so a later pass skips them).
* ``tap_intel_fight`` end-to-end — a second run over the same board taps a
  *different* pin once the first one is recorded, letting a free march start the
  next event instead of colliding with the in-flight one.
"""
from __future__ import annotations

import importlib.util
from collections import namedtuple
from pathlib import Path
from typing import Any

import cv2
import pytest
from games.wos.intel import started

from tasks.dsl_exec.context import DslExecContext

MODULE_DIR = Path(__file__).resolve().parents[1]

_Center = namedtuple("_Center", ["x", "y"])


class _Marker:
    """Minimal duck-type for the pure filter (only ``.center`` is read)."""

    def __init__(self, x: int, y: int) -> None:
        self.center = _Center(x, y)


# --- pure filter -------------------------------------------------------------


def test_partition_no_started_keeps_all() -> None:
    markers = [_Marker(10, 10), _Marker(200, 200)]
    fresh, suppressed = started.partition_markers(markers, [])
    assert fresh == markers
    assert suppressed == []


def test_partition_suppresses_pin_near_started_keeps_far() -> None:
    near = _Marker(100, 100)       # exactly on a started coord
    jitter = _Marker(120, 100)     # 20 px away → same pin
    far = _Marker(300, 300)        # well clear → a different pin
    ev = started.StartedEvent(x=100, y=100, started_at=0.0)

    fresh, suppressed = started.partition_markers(
        [near, jitter, far], [ev], radius=40
    )

    assert far in fresh and near not in fresh and jitter not in fresh
    assert set(suppressed) == {near, jitter}


def test_is_near_started_returns_match_or_none() -> None:
    ev = started.StartedEvent(x=50, y=50, started_at=0.0)
    assert started.is_near_started(60, 50, [ev], radius=40) is ev
    assert started.is_near_started(200, 200, [ev], radius=40) is None
    # Just outside the radius (41 px) is a miss; on the boundary (40) is a hit.
    assert started.is_near_started(91, 50, [ev], radius=40) is None
    assert started.is_near_started(90, 50, [ev], radius=40) is ev


# --- TTL-bounded Redis store -------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.expires: dict[str, int] = {}

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, field: str, value: Any) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.get(key, {})
        removed = 0
        for f in fields:
            if f in bucket:
                del bucket[f]
                removed += 1
        return removed

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True


@pytest.mark.asyncio
async def test_record_then_load_round_trips() -> None:
    redis = _FakeRedis()
    assert await started.record_started(redis, "p1", 123, 456, now=1000.0)

    active = await started.load_started(redis, "p1", now=1000.5)

    assert [(e.x, e.y) for e in active] == [(123, 456)]
    assert active[0].started_at == 1000.0
    # Whole-key backstop TTL was set.
    assert redis.expires[started.started_key("p1")] == started.STARTED_TTL_SECONDS


@pytest.mark.asyncio
async def test_load_prunes_and_forgets_stale_entries() -> None:
    redis = _FakeRedis()
    await started.record_started(redis, "p1", 1, 1, now=0.0)        # old
    await started.record_started(redis, "p1", 2, 2, now=595.0)      # fresh

    # now=600.1 → entry@0 is 600.1 s old (> 600 TTL) and dropped; @595 survives.
    active = await started.load_started(redis, "p1", now=600.1, ttl=600)

    assert [(e.x, e.y) for e in active] == [(2, 2)]
    # The stale field was deleted from Redis, not just filtered out.
    assert "1,1" not in redis.hashes[started.started_key("p1")]
    assert "2,2" in redis.hashes[started.started_key("p1")]


class _BytesFakeRedis:
    """Mimics an aioredis client without decode_responses: hgetall returns bytes.

    Guards the prune path: the stale field handed to hdel must match the
    byte-exact field hgetall returned (regression for decoding it to str first).
    """

    def __init__(self, fields: dict[bytes, bytes]) -> None:
        self.fields = dict(fields)
        self.hdel_args: list[Any] = []

    async def hgetall(self, _key: str) -> dict[bytes, bytes]:
        return dict(self.fields)

    async def hdel(self, _key: str, *fields: Any) -> int:
        self.hdel_args.extend(fields)
        removed = 0
        for f in fields:
            if f in self.fields:
                del self.fields[f]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_prune_matches_byte_exact_field() -> None:
    # A bytes-returning client (the real worker config). The stale entry must be
    # deleted with the byte-exact field, not a decoded copy that wouldn't match.
    redis = _BytesFakeRedis({b"1,1": b"0.0", b"2,2": b"595.0"})

    active = await started.load_started(redis, "p1", now=600.1, ttl=600)

    assert [(e.x, e.y) for e in active] == [(2, 2)]
    assert redis.hdel_args == [b"1,1"]        # byte-exact, matched the store
    assert b"1,1" not in redis.fields          # actually removed
    assert b"2,2" in redis.fields


@pytest.mark.asyncio
async def test_record_and_load_noop_without_player() -> None:
    redis = _FakeRedis()
    assert await started.record_started(redis, "", 1, 1, now=0.0) is False
    assert await started.load_started(redis, "", now=0.0) == []
    assert await started.load_started(None, "p1", now=0.0) == []


# --- end-to-end: a second run skips the started pin --------------------------


def _load_exec() -> Any:
    spec = importlib.util.spec_from_file_location(
        "intel_exec_dedup_test", MODULE_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXEC = _load_exec()


class _ExecFakeRedis:
    """hget/hgetall/hset/hdel/expire — enough for tap_intel_fight + started."""

    def __init__(self, stamina: str) -> None:
        self.hashes: dict[str, dict[str, Any]] = {"wos:player:p1:state": {"stamina": stamina}}

    async def hget(self, key: str, field: str) -> Any:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, field: str | None = None, value: Any = None,
                   mapping: dict[str, Any] | None = None) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update(mapping)
            return len(mapping)
        assert field is not None
        bucket[field] = value
        return 1

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.get(key, {})
        return sum(1 for f in fields if bucket.pop(f, None) is not None)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


class _FakeActions:
    def __init__(self, image: Any) -> None:
        self._image = image
        self.taps: list[Any] = []

    def capture_screen_bgr(self, _instance_id: str) -> Any:
        return self._image

    def tap(self, _instance_id: str, point: Any, **_kwargs: Any) -> bool:
        self.taps.append(point)
        return True


def _camp_frame() -> Any:
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None
    return image


def _ctx(redis: Any) -> DslExecContext:
    return DslExecContext(redis_client=redis, player_id="p1", instance_id="i1", args={})


@pytest.mark.asyncio
async def test_second_run_skips_the_started_pin(monkeypatch) -> None:
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)
    redis = _ExecFakeRedis("100")

    # First run: taps the best pin and remembers it.
    await EXEC._exec_tap_intel_fight(_ctx(redis))
    assert actions.taps, "first run should tap a pin"
    first_x, first_y = actions.taps[-1].x, actions.taps[-1].y
    assert started.started_key("p1") in redis.hashes  # it was recorded

    # Second run over the SAME board: the started pin is suppressed, so a
    # different pin is tapped (a free march can start the next event).
    res2: dict[str, Any] = {}
    ctx2 = DslExecContext(
        redis_client=redis, player_id="p1", instance_id="i1", args={}, result=res2
    )
    await EXEC._exec_tap_intel_fight(ctx2)

    assert res2["suppressed"] >= 1
    assert res2["started_active"] >= 1
    if res2["action"] == "tapped":
        assert (res2["tap_x"], res2["tap_y"]) != (first_x, first_y)
    else:
        # If only one worthwhile pin existed, the run cleanly declines instead of
        # re-tapping the in-flight one.
        assert res2["action"] in {"all_in_progress", "skipped"}


@pytest.mark.asyncio
async def test_track_started_false_disables_dedup(monkeypatch) -> None:
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)
    redis = _ExecFakeRedis("100")

    ctx = DslExecContext(
        redis_client=redis, player_id="p1", instance_id="i1",
        args={"track_started": False}, result={},
    )
    await EXEC._exec_tap_intel_fight(ctx)

    # Nothing recorded when tracking is off.
    assert started.started_key("p1") not in redis.hashes
    assert ctx.result.get("started_recorded") in (False, None)
