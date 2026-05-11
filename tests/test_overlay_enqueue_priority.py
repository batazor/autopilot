from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from worker.instance_worker_overlay import InstanceWorkerOverlayMixin

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
async def test_overlay_enqueues_all_matched_payloads() -> None:
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
        }
    )

    # Enqueue order is irrelevant — `RedisQueue.pop_due` sorts by (-priority, run_at).
    assert {c["task_type"] for c in worker._queue.calls} == {
        "hand_pointer_small",
        "skip_text_button",
    }
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
        }
    )

    assert [c["task_type"] for c in worker._queue.calls] == ["skip_text_button"]
    assert worker._queue.calls[0].get("dedup_ignore_region") is True


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
    raw = await redis_async.hgetall(key)  # type: ignore[attr-defined]
    assert raw["chapter.task"] == "Bunk Beds in Shelter 2"
    assert raw["chapter.task_text"] == "Bunk Beds in Shelter 2"
    assert raw["chapter.task_confidence"] == "0.9542"


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
    cur = await redis_async.hget(key, "current_screen")  # type: ignore[attr-defined]
    assert cur == "building"
