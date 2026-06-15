"""MARCH dispatch: turn coordinate() commits into queued scenarios."""
from __future__ import annotations

from typing import Any

import pytest
from games.wos.core.coordinator import MARCH, CandidateAction, Commit, CoordinatorDecision
from games.wos.core.coordinator.dispatch import (
    DISPATCH_PRIORITY_BASE,
    MarchScenario,
    dispatch_march,
)


class _FakeQueue:
    """Captures schedule() calls; returns per-task_type enqueue results."""

    def __init__(self, results: dict[str, bool] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self._results.get(kwargs.get("task_type"), True)


def _march_action(domain: str, *, priority: float, cost: dict[str, int] | None = None):
    return CandidateAction(
        domain=domain,
        channel_kind=MARCH,
        key=f"{domain}:k",
        priority=priority,
        cost=cost or {},
    )


def _decision(*commits: Commit) -> CoordinatorDecision:
    return CoordinatorDecision(
        commits=tuple(commits),
        starved=(),
        no_channel=(),
        remaining={},
        bottleneck_resources=(),
    )


@pytest.mark.asyncio
async def test_dispatches_committed_intel_as_intel_run():
    queue = _FakeQueue()
    decision = _decision(Commit("march_1", _march_action("intel", priority=760.0, cost={"stamina": 10})))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["task_type"] == "intel_run"
    assert call["dsl_scenario"] == "intel_run"
    assert call["player_id"] == "p1"
    assert call["instance_id"] == "i1"
    assert call["skip_if_duplicate"] is True
    # Cross-domain priority lifted into the queue's absolute band.
    assert call["priority"] == DISPATCH_PRIORITY_BASE + 760
    assert [e.task_type for e in result.enqueued] == ["intel_run"]
    assert result.skipped == ()


@pytest.mark.asyncio
async def test_domain_without_scenario_is_skipped_not_dispatched():
    queue = _FakeQueue()
    # gather has no scenario in the default registry yet.
    decision = _decision(Commit("march_1", _march_action("gather", priority=720.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert queue.calls == []
    assert result.enqueued == ()
    assert [(s.domain, s.reason) for s in result.skipped] == [("gather", "no_scenario")]


@pytest.mark.asyncio
async def test_duplicate_in_flight_is_reported_skipped():
    queue = _FakeQueue(results={"intel_run": False})  # already queued
    decision = _decision(Commit("march_1", _march_action("intel", priority=760.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert len(queue.calls) == 1
    assert result.enqueued == ()
    assert [(s.domain, s.reason) for s in result.skipped] == [("intel", "duplicate")]


@pytest.mark.asyncio
async def test_no_commits_schedules_nothing():
    queue = _FakeQueue()
    result = await dispatch_march(
        _decision(), queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )
    assert queue.calls == []
    assert result.enqueued == ()
    assert result.skipped == ()


@pytest.mark.asyncio
async def test_custom_registry_dispatches_gather():
    queue = _FakeQueue()
    registry = {"gather": MarchScenario(task_type="gather_run", dsl_scenario="gather_run")}
    decision = _decision(Commit("march_2", _march_action("gather", priority=720.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=2000.0,
        scenarios=registry,
    )

    assert queue.calls[0]["task_type"] == "gather_run"
    assert queue.calls[0]["priority"] == DISPATCH_PRIORITY_BASE + 720
    assert [e.task_type for e in result.enqueued] == ["gather_run"]
