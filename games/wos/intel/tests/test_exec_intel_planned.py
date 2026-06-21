"""The Intel planner wired into the live tap path (``tap_intel_fight``).

``select_planned_marker`` is the bridge between the cv2 detector and the pure
value-greedy planner: it ranks visible markers by loot value, spends the stamina
budget on the best one, and *declines* (returns ``None``) when the run isn't
worth it. These tests exercise that decision directly, then drive the exec
handler end-to-end against a real reference frame with a fake device + Redis.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import cv2
import pytest
from games.wos.intel.planner import (
    INSUFFICIENT_STAMINA,
    QUOTA_FULL,
    SELECTED,
)

from tasks.dsl_exec.context import DslExecContext

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_exec_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "intel_exec_planned_test",
        MODULE_DIR / "exec.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXEC = _load_exec_module()


def _marker(kind: str, color: str, *, x: int = 0, y: int = 0, score: float = 0.9):
    return EXEC.IntelMarker(x=x, y=y, w=10, h=10, score=score, kind=kind, color=color)


# --- select_planned_marker: the value-greedy budget decision -----------------


def test_picks_highest_value_with_ample_stamina() -> None:
    purple_fight = _marker("fight", "purple", x=0)
    gold_horned = _marker("skull_horned", "gold", x=40)

    marker, trace = EXEC.select_planned_marker(
        [purple_fight, gold_horned], stamina=100.0
    )

    assert marker is gold_horned  # 1.0*1.3 beats 0.65*1.0
    assert trace["reason"] == SELECTED
    assert trace["rank"] == 1


def test_falls_back_to_pick_marker_without_stamina() -> None:
    # No stamina signal → deterministic colour>kind>score pick (prior behaviour).
    purple_special = _marker("skull_horned", "purple", x=20, score=1.0)
    gold_fight = _marker("fight", "gold", x=0, score=0.7)

    marker, trace = EXEC.select_planned_marker(
        [purple_special, gold_fight], stamina=None
    )

    assert marker is gold_fight  # gold colour wins before kind/score
    assert trace["reason"] == "no_stamina_signal"


def test_declines_when_stamina_below_cost() -> None:
    marker, trace = EXEC.select_planned_marker(
        [_marker("fight", "gold")], stamina=5.0, cost=10
    )

    assert marker is None
    assert trace["reason"] == INSUFFICIENT_STAMINA


def test_declines_when_daily_quota_exhausted() -> None:
    marker, trace = EXEC.select_planned_marker(
        [_marker("fight", "gold")], stamina=100.0, daily_quota_left=0
    )

    assert marker is None
    assert trace["reason"] == QUOTA_FULL


def test_reserve_holds_back_stamina() -> None:
    gold = _marker("fight", "gold")

    # 10 stamina, but 5 reserved for Joe → only 5 spendable, can't afford a 10 pin.
    held, trace = EXEC.select_planned_marker([gold], stamina=10.0, reserve=5, cost=10)
    assert held is None
    assert trace["reason"] == INSUFFICIENT_STAMINA

    # Same stamina with nothing reserved clears it.
    taken, _ = EXEC.select_planned_marker([gold], stamina=10.0, reserve=0, cost=10)
    assert taken is gold


def test_no_markers_returns_none() -> None:
    marker, trace = EXEC.select_planned_marker([], stamina=100.0)
    assert marker is None
    assert trace["reason"] == "no_markers"


def test_parse_march_ttl_seconds() -> None:
    assert EXEC.parse_march_ttl_seconds("00:01:21") == 81
    assert EXEC.parse_march_ttl_seconds("1:21") == 81
    assert EXEC.parse_march_ttl_seconds("81") == 81
    assert EXEC.parse_march_ttl_seconds("") is None


# --- the exec handler end-to-end against a real frame ------------------------


class _FakeRedis:
    def __init__(
        self,
        stamina: str | None,
        *,
        hashes: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._stamina = stamina
        self.hashes = hashes or {}
        if stamina is not None:
            self.hashes.setdefault("wos:player:p1:state", {})["stamina"] = stamina
        self.hset_calls: list[tuple[str, str | None, Any, dict[str, Any] | None]] = []

    async def hget(self, key: str, field: str) -> Any:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        self.hset_calls.append((key, field, value, mapping))
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update(mapping)
            return len(mapping)
        assert field is not None
        bucket[field] = value
        return 1


class _FakeActions:
    def __init__(self, image: Any) -> None:
        self._image = image
        self.taps: list[Any] = []

    def capture_screen_bgr(self, _instance_id: str) -> Any:
        return self._image

    def tap(self, _instance_id: str, point: Any, **_kwargs: Any) -> bool:
        self.taps.append(point)
        return True


def _ctx(redis: Any, args: dict[str, Any] | None = None) -> DslExecContext:
    return DslExecContext(
        redis_client=redis,
        player_id="p1",
        instance_id="i1",
        args=args or {},
    )


def _camp_frame() -> Any:
    image = cv2.imread(str(MODULE_DIR / "references" / "camp.png"))
    assert image is not None
    return image


@pytest.mark.asyncio
async def test_handler_taps_planned_gold_marker(monkeypatch) -> None:
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)

    ctx = _ctx(_FakeRedis("100"))
    await EXEC._exec_tap_intel_fight(ctx)

    assert ctx.result["action"] == "tapped"
    assert ctx.result["color"] == "gold"
    assert ctx.result["kind"] in {"skull_horned", "camp"}
    assert ctx.result["reason"] == SELECTED
    assert len(actions.taps) == 1
    assert (actions.taps[0].x, actions.taps[0].y) == (ctx.result["tap_x"], ctx.result["tap_y"])


@pytest.mark.asyncio
async def test_handler_skips_when_stamina_insufficient(monkeypatch) -> None:
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)

    ctx = _ctx(_FakeRedis("5"))  # below the default 10-per-marker cost
    await EXEC._exec_tap_intel_fight(ctx)

    assert ctx.result["action"] == "skipped"
    assert ctx.result["reason"] == INSUFFICIENT_STAMINA
    assert actions.taps == []


@pytest.mark.asyncio
async def test_handler_taps_when_stamina_unknown(monkeypatch) -> None:
    # No stamina in Redis → fall back to the deterministic pick (never worse).
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)

    ctx = _ctx(_FakeRedis(None))
    await EXEC._exec_tap_intel_fight(ctx)

    assert ctx.result["action"] == "tapped"
    assert ctx.result["reason"] == "no_stamina_signal"
    assert len(actions.taps) == 1


@pytest.mark.asyncio
async def test_handler_holds_joe_reserve_from_calendar(monkeypatch) -> None:
    # Crazy Joe live-or-imminent → joe_event_active=1 (fed by the calendar) →
    # hold joe_bandits' 50 reserve_floor. 56 − 50 = 6 spendable, below a 10-cost
    # pin, so the run is skipped (mirrors the planner's reserve test).
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)

    redis = _FakeRedis("56", hashes={"wos:player:p1:state": {"joe_event_active": "1"}})
    ctx = _ctx(redis)
    await EXEC._exec_tap_intel_fight(ctx)

    assert ctx.result["action"] == "skipped"
    assert ctx.result["reason"] == INSUFFICIENT_STAMINA
    assert ctx.result["reserve"] == 50
    assert actions.taps == []


@pytest.mark.asyncio
async def test_handler_no_reserve_when_joe_off(monkeypatch) -> None:
    # Joe window closed → no reserve → the same 56 stamina clears a 10-cost pin.
    actions = _FakeActions(_camp_frame())
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)

    ctx = _ctx(_FakeRedis("56"))
    await EXEC._exec_tap_intel_fight(ctx)

    assert ctx.result["action"] == "tapped"
    assert ctx.result["reserve"] == 0
    assert len(actions.taps) == 1


@pytest.mark.asyncio
async def test_confirm_intel_march_lease_extends_resource_reservation(monkeypatch) -> None:
    monkeypatch.setattr(EXEC.time, "time", lambda: 1000.0)
    key = "wos:player:p1:resource_reservations"
    redis = _FakeRedis(
        "100",
        hashes={
            "wos:player:p1:state": {"intel.march_ttl": "00:01:21"},
            key: {
                "intel:1": json.dumps(
                    {
                        "id": "intel:1",
                        "action_id": "intel_run",
                        "slots": 1,
                        "confirm_by": 1050.0,
                        "expires_at": 1050.0,
                        "confirmed": False,
                    }
                )
            },
        },
    )
    ctx = _ctx(redis, {"resource_reservation": "intel:1"})

    await EXEC._exec_confirm_intel_march_lease(ctx)

    entry = json.loads(redis.hashes[key]["intel:1"])
    assert entry["confirmed"] is True
    assert entry["expires_at"] == 1000.0 + 177
    assert entry["lease_seconds"] == 177
    assert entry["ttl_seconds"] == 81
    assert entry["source"] == "intel.deploy"
    assert redis.hashes["wos:player:p1:state"]["intel.march_ttl_seconds"] == "81"
    assert redis.hashes["wos:player:p1:state"]["intel.march_lease_seconds"] == "177"
    assert ctx.result["action"] == "lease_confirmed"
    assert ctx.result["ttl_seconds"] == 81
    assert ctx.result["lease_seconds"] == 177


@pytest.mark.asyncio
async def test_confirm_intel_march_lease_records_fallback_when_no_reservation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(EXEC.time, "time", lambda: 2000.0)
    key = "wos:player:p1:resource_reservations"
    redis = _FakeRedis(
        "100",
        hashes={"wos:player:p1:state": {"intel.march_ttl": "01:00"}},
    )
    ctx = _ctx(redis)

    await EXEC._exec_confirm_intel_march_lease(ctx)

    entry = json.loads(redis.hashes[key]["intel_run:manual:2000"])
    assert entry["confirmed"] is True
    assert entry["slots"] == 1
    assert entry["expires_at"] == 2000.0 + 135
    assert entry["source"] == "intel.deploy"
    assert redis.hashes["wos:player:p1:state"]["intel.march_ttl_seconds"] == "60"
    assert redis.hashes["wos:player:p1:state"]["intel.march_lease_seconds"] == "135"
    assert ctx.result["action"] == "lease_recorded"
    assert ctx.result["reservation"] == "intel_run:manual:2000"
