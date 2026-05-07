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
                "pushScenario": [{"name": "skip_text_button", "priority": 60000}],
            },
            "hand_pointer_small.visible": {
                "matched": True,
                "region": "hand_pointer_small",
                "pushScenario": [{"name": "hand_pointer_small", "priority": 62000}],
            },
        }
    )

    assert [c["task_type"] for c in worker._queue.calls] == [
        "hand_pointer_small",
        "skip_text_button",
    ]
