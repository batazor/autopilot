from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

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


@pytest.mark.asyncio
async def test_overlay_enqueues_highest_priority_matched_payload_only() -> None:
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

    assert [c["task_type"] for c in worker._queue.calls] == ["hand_pointer_small"]
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
    module_dir = tmp_path / "modules" / "core" / "test_scenarios"
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
