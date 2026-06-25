from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from worker.instance_worker_overlay import InstanceWorkerOverlayMixin

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


class _Worker(InstanceWorkerOverlayMixin):
    def __init__(self) -> None:
        self._cfg = SimpleNamespace(instance_id="bs1")
        self._redis = None
        self._queue = _FakeQueue()

    async def _focus_scenario(self) -> str:
        # Collaborator the overlay mixin expects from the full worker host
        # (instance_worker_redis). "" = focus mode off, so pushes proceed.
        return ""


class _FakeCounter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, Any]]] = []

    def add(self, value: int, *, attributes: dict[str, Any]) -> None:
        self.calls.append((value, attributes))


@pytest.mark.asyncio
async def test_overlay_enqueues_all_matched_payloads_in_priority_order() -> None:
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "skip_text_button.visible": {
                "matched": True,
                "region": "skip_text_button",
                "pushScenario": [{"name": "skip_text_button", "priority": 85_000}],
            },
            "hand_pointer_small.visible": {
                "matched": True,
                "region": "hand_pointer_small",
                "pushScenario": [{"name": "hand_pointer_small", "priority": 86_000}],
            },
        },
        active_player="p1",
    )

    assert [c["task_type"] for c in worker._queue.calls] == [
        "hand_pointer_small",
        "skip_text_button",
    ]
    assert all(c.get("dedup_ignore_region") is True for c in worker._queue.calls)


@pytest.mark.asyncio
async def test_overlay_enqueue_skips_unmatched_payloads() -> None:
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "hand_pointer.visible": {
                "matched": False,
                "region": "hand_pointer",
                "pushScenario": [{"name": "hand_pointer", "priority": 86_000}],
            },
            "skip_text_button.visible": {
                "matched": True,
                "region": "skip_text_button",
                "pushScenario": [{"name": "skip_text_button", "priority": 85_000}],
            },
        },
        active_player="p1",
    )

    assert [c["task_type"] for c in worker._queue.calls] == ["skip_text_button"]
    assert worker._queue.calls[0].get("dedup_ignore_region") is True


@pytest.mark.asyncio
async def test_overlay_enqueue_preserves_push_scenario_args() -> None:
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "deals.tabs.visible_red_dot": {
                "matched": True,
                "region": "deals.tabs_strip",
                "pushScenario": [
                    {
                        "type": "tabs.strip.advance",
                        "priority": 70_000,
                        "args": {"region": "deals.tabs_strip"},
                    }
                ],
            },
        },
        active_player="p1",
    )

    assert len(worker._queue.calls) == 1
    assert worker._queue.calls[0]["task_type"] == "tabs.strip.advance"
    assert worker._queue.calls[0]["args"] == {"region": "deals.tabs_strip"}


@pytest.mark.asyncio
async def test_overlay_enqueue_schedules_all_targets_in_one_payload() -> None:
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "deals.tabs.visible_red_dot": {
                "matched": True,
                "region": "deals.tabs_strip",
                "pushScenario": [
                    {"type": "deals.dead_shot", "priority": 80_000},
                    {"type": "deals.tundra_trading_station", "priority": 80_000},
                ],
            },
        },
        active_player="p1",
    )

    assert [c["task_type"] for c in worker._queue.calls] == [
        "deals.dead_shot",
        "deals.tundra_trading_station",
    ]


@pytest.mark.asyncio
async def test_overlay_logs_idle_tab_red_dot_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = _Worker()
    counter = _FakeCounter()
    monkeypatch.setattr(
        "worker.instance_worker_overlay.overlay_tab_red_dot_idle_counter",
        lambda: counter,
    )
    monkeypatch.setattr(
        "worker.instance_worker_overlay._IDLE_TAB_RED_DOT_LAST_LOG",
        {},
    )
    caplog.set_level(logging.INFO)

    await worker._schedule_overlay_matches(
        {
            "deals.tabs.visible_red_dot": {
                "matched": True,
                "action": "detectTabs",
                "current_screen": "deals.hero_rally",
                "region": "deals.tabs_strip",
                "red_dot_indices": [0, 2],
                "active_index": 1,
                "active_page_id": "deals.hero_rally",
                "red_dot_pages": ["deals.hall_of_heroes"],
                "tab_action": "push_red_dot_pages",
                "pushScenario": [
                    {"type": "deals.hall_of_heroes", "priority": 80_000}
                ],
            },
        },
        active_player="p1",
    )

    assert counter.calls == [
        (
            1,
            {
                "instance_id": "bs1",
                "screen": "deals.hero_rally",
                "rule": "deals.tabs.visible_red_dot",
                "region": "deals.tabs_strip",
                "active_index": "1",
                "red_dot_indices": "0,2",
                "action": "push_red_dot_pages",
            },
        )
    ]
    assert any(
        "overlay detectTabs idle red dots" in rec.message
        and "screen=deals.hero_rally" in rec.message
        and "red_dot_indices=0,2" in rec.message
        and "action=push_red_dot_pages" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_overlay_enqueue_skips_player_bound_scenario_without_active_player() -> None:
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "isWorkers.visible": {
                "matched": True,
                "region": "isWorkers",
                "pushScenario": [{"name": "assign_worker", "priority": 80_000}],
            },
        },
        active_player="",
    )

    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_overlay_enqueue_skips_disabled_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenarios_dir = module_dir / "scenarios"
    scenarios_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenarios_dir / "disabled_popup.yaml").write_text(
        "enabled: false\nname: Disabled popup\nsteps: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("worker.instance_worker_overlay._REPO_ROOT", tmp_path)

    worker = _Worker()
    await worker._schedule_overlay_matches(
        {
            "disabled.visible": {
                "matched": True,
                "region": "disabled",
                "pushScenario": [{"name": "disabled_popup", "priority": 85_000}],
            },
        }
    )

    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_overlay_text_enqueue_writes_region_text_to_instance_state(redis_async: object) -> None:
    worker = _Worker()
    worker._redis = redis_async  # type: ignore[assignment]

    await worker._schedule_overlay_matches(
        {
            "chapter.task.present": {
                "matched": True,
                "region": "chapter.task",
                "action": "text",
                "text": "Bunk Beds in Shelter 2",
                "confidence": 0.9542,
                "pushScenario": [{"name": "chapter_task_router", "priority": 70000}],
            },
        }
    )

    key = "wos:instance:bs1:state"
    raw = await redis_async.hgetall(key)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert raw["chapter.task"] == "Bunk Beds in Shelter 2"
    assert raw["chapter.task_text"] == "Bunk Beds in Shelter 2"
    assert raw["chapter.task_confidence"] == "0.9542"


@pytest.mark.asyncio
async def test_push_ttl_throttles_repeat_push_per_player(redis_async: object) -> None:
    """``pushScenario.ttl`` blocks a second push of the same task while the key is alive."""
    worker = _Worker()
    worker._redis = redis_async  # type: ignore[assignment]

    results = {
        "page.worker.add.visible": {
            "matched": True,
            "region": "page.worker.add",
            "pushScenario": [{"name": "assign_worker", "priority": 80_000, "ttl": 300}],
        },
    }

    await worker._schedule_overlay_matches(results, active_player="p1")
    await worker._schedule_overlay_matches(results, active_player="p1")

    assert [c["task_type"] for c in worker._queue.calls] == ["assign_worker"]
    assert await redis_async.exists("wos:player:p1:push_ttl:assign_worker") == 1  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    # Different player on the same instance is not blocked by p1's throttle.
    await worker._schedule_overlay_matches(results, active_player="p2")
    assert [c["task_type"] for c in worker._queue.calls] == ["assign_worker", "assign_worker"]


@pytest.mark.asyncio
async def test_push_ttl_absent_does_not_throttle(redis_async: object) -> None:
    worker = _Worker()
    worker._redis = redis_async  # type: ignore[assignment]

    results = {
        "skip_text_button.visible": {
            "matched": True,
            "region": "skip_text_button",
            "pushScenario": [{"name": "skip_text_button", "priority": 85_000}],
        },
    }

    await worker._schedule_overlay_matches(results, active_player="p1")
    await worker._schedule_overlay_matches(results, active_player="p1")

    assert [c["task_type"] for c in worker._queue.calls] == [
        "skip_text_button",
        "skip_text_button",
    ]


@pytest.mark.asyncio
async def test_overlay_set_node_writes_current_screen_to_instance_state(redis_async: object) -> None:
    worker = _Worker()
    worker._redis = redis_async  # type: ignore[assignment]

    await worker._schedule_overlay_matches(
        {
            "building.visible": {
                "matched": True,
                "region": "page.building.furniture",
                "action": "findIcon",
                "set_node": "building",
            },
        }
    )

    key = "wos:instance:bs1:state"
    cur = await redis_async.hget(key, "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "building"


# A real device-level scenario, so ``dsl_scenario_yaml_device_level`` is True.
_DEVICE_LEVEL_SCENARIO = "onboarding.click.skip_button"


def _running(worker: _Worker, task_type: str | None) -> list[str]:
    """Mark ``task_type`` as the in-flight task and record cancel calls."""
    cancelled: list[str] = []

    async def _cancel(reason: str, **kwargs: Any) -> bool:
        cancelled.append(reason)
        return True

    worker._current_task_type = task_type  # type: ignore[attr-defined]
    worker._current_task_handle = SimpleNamespace(done=lambda: False)  # type: ignore[attr-defined]
    worker._cancel_current_task = _cancel  # type: ignore[attr-defined]
    return cancelled


@pytest.mark.asyncio
async def test_device_level_push_skips_when_same_scenario_already_running() -> None:
    """The running scenario must not preempt itself (preempted_by_device_level)."""
    worker = _Worker()
    cancelled = _running(worker, _DEVICE_LEVEL_SCENARIO)

    await worker._schedule_overlay_matches(
        {
            "skip_button.visible": {
                "matched": True,
                "region": "skip_button",
                "pushScenario": [{"name": _DEVICE_LEVEL_SCENARIO, "priority": 100_000}],
            },
        },
        active_player="",
    )

    assert worker._queue.calls == []  # no duplicate enqueue
    assert cancelled == []  # running instance not preempted


@pytest.mark.asyncio
async def test_device_level_push_proceeds_when_different_scenario_running() -> None:
    """A different in-flight scenario is still preempted to make room."""
    worker = _Worker()
    cancelled = _running(worker, "some.other.scenario")

    await worker._schedule_overlay_matches(
        {
            "skip_button.visible": {
                "matched": True,
                "region": "skip_button",
                "pushScenario": [{"name": _DEVICE_LEVEL_SCENARIO, "priority": 100_000}],
            },
        },
        active_player="",
    )

    assert [c["task_type"] for c in worker._queue.calls] == [_DEVICE_LEVEL_SCENARIO]
    assert len(cancelled) == 1


@pytest.mark.asyncio
async def test_player_bound_push_enqueues_under_active_player() -> None:
    """A player-bound scenario is enqueued with the active player (not ""), so the
    queue's dedup + recent-run ranking treat it per-player. With "" every
    account's push collapses into one device-level signature — wrong for a
    multi-account device."""
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "page.worker.add.visible": {
                "matched": True,
                "region": "page.worker.add",
                "pushScenario": [{"name": "assign_worker", "priority": 80_000}],
            },
        },
        active_player="p1",
    )

    assert [c["task_type"] for c in worker._queue.calls] == ["assign_worker"]
    assert worker._queue.calls[0]["player_id"] == "p1"


@pytest.mark.asyncio
async def test_device_level_push_enqueues_with_empty_player_even_when_active_known() -> None:
    """A ``device_level: true`` scenario stays player-agnostic (``player_id=""``)
    even when an active player is known — the worker resolves a player at pop
    time only if the task needs one."""
    worker = _Worker()

    await worker._schedule_overlay_matches(
        {
            "skip_button.visible": {
                "matched": True,
                "region": "skip_button",
                "pushScenario": [{"name": _DEVICE_LEVEL_SCENARIO, "priority": 100_000}],
            },
        },
        active_player="p1",
    )

    assert [c["task_type"] for c in worker._queue.calls] == [_DEVICE_LEVEL_SCENARIO]
    assert worker._queue.calls[0]["player_id"] == ""
