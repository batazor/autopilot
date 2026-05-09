from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from worker.instance_worker_overlay import InstanceWorkerOverlayMixin


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


class _FakeRedis:
    def __init__(self) -> None:
        self.hsets: list[tuple[str, dict[str, str]]] = []

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hsets.append((key, mapping))


class _Worker(InstanceWorkerOverlayMixin):
    def __init__(self) -> None:
        self._cfg = SimpleNamespace(instance_id="bs1")
        self._redis = None
        self._queue = _FakeQueue()


@pytest.mark.asyncio
async def test_overlay_enqueue_orders_matched_payloads_by_priority() -> None:
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

    assert [c["task_type"] for c in worker._queue.calls] == [
        "hand_pointer_small",
        "skip_text_button",
    ]


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


@pytest.mark.asyncio
async def test_overlay_text_enqueue_writes_region_text_to_instance_state() -> None:
    worker = _Worker()
    worker._redis = _FakeRedis()

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

    assert worker._redis.hsets
    key, mapping = worker._redis.hsets[0]
    assert key == "wos:instance:bs1:state"
    assert mapping["chapter.task"] == "Bunk Beds in Shelter 2"
    assert mapping["chapter.task_text"] == "Bunk Beds in Shelter 2"
    assert mapping["chapter.task_confidence"] == "0.9542"


@pytest.mark.asyncio
async def test_overlay_set_node_writes_current_screen_to_instance_state() -> None:
    worker = _Worker()
    worker._redis = _FakeRedis()

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

    assert worker._redis.hsets
    key, mapping = worker._redis.hsets[0]
    assert key == "wos:instance:bs1:state"
    assert mapping["current_screen"] == "building"
