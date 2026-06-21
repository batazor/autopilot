"""End-to-end worker-side directive flow against a real Redis (testcontainers).

Marked ``integration`` — skips without Docker. Proves a posted directive is
drained by ``CoordWorkerMixin``, dispatched to its handler (which enqueues a
scenario via the queue, never tapping directly), and its status round-trips.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from coord import CoordWorkerMixin, Directive, DirectiveBus, DirectiveTarget
from coord.models import STATUS_DONE, FleetView

pytestmark = pytest.mark.integration


class _FakeQueue:
    """Records schedule() calls instead of touching the real queue."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def schedule(self, **kw):
        self.calls.append(kw)
        return True


class _FakeWorker(CoordWorkerMixin):
    def __init__(self, redis, instance_id, queue) -> None:
        self._cfg = SimpleNamespace(instance_id=instance_id, game="wos")
        self._redis = redis
        self._queue = queue


async def test_directive_drains_into_queue_and_status(redis_async):
    queue = _FakeQueue()
    worker = _FakeWorker(redis_async, "dev-a", queue)
    bus = DirectiveBus(redis_async)

    directive = Directive(
        directive_id="d-enq-1",
        kind="enqueue_scenario",
        target=DirectiveTarget.instance("dev-a"),
        payload={"scenario": "event.gather", "player_id": "111"},
        idempotency_key="run1:0:111:run_scenario",
    )
    posted = await bus.post(directive, FleetView(()), now=1.0)
    assert posted == ["dev-a"]

    await worker._drain_directives()

    # the handler enqueued the scenario (no direct tap)
    assert len(queue.calls) == 1
    assert queue.calls[0]["dsl_scenario"] == "event.gather"
    assert queue.calls[0]["player_id"] == "111"

    # status round-trips to done
    st = await bus.read_status("d-enq-1")
    assert st is not None and st.state == STATUS_DONE and st.result == "queued"


async def test_directive_dedup_prevents_double_dispatch(redis_async):
    queue = _FakeQueue()
    worker = _FakeWorker(redis_async, "dev-a", queue)
    bus = DirectiveBus(redis_async)
    d = Directive("d-dup", "enqueue_scenario", DirectiveTarget.instance("dev-a"),
                  payload={"scenario": "event.claim"}, idempotency_key="k-dup")

    # post the same directive twice; the worker should run it once
    await bus.post(d, FleetView(()), now=1.0)
    await bus.post(d, FleetView(()), now=1.0)
    await worker._drain_directives()
    assert len(queue.calls) == 1


async def test_ping_directive_marks_done(redis_async):
    worker = _FakeWorker(redis_async, "dev-a", None)
    bus = DirectiveBus(redis_async)
    d = Directive("d-ping", "ping", DirectiveTarget.instance("dev-a"))
    await bus.post(d, FleetView(()), now=1.0)
    await worker._drain_directives()
    st = await bus.read_status("d-ping")
    assert st is not None and st.state == STATUS_DONE and st.result == "pong"
