"""Unit tests for the march decision-trace writer (``dispatch.write_march_trace``).

No real Redis: a fake pipeline records the ``zadd`` so we can assert the payload
shape and the signature gate. Mirrors the stamina/resources adapter trace tests.
"""

from __future__ import annotations

import json

import pytest

from games.wos.core.coordinator import dispatch as d

NOW = 1_700_000_000.0


class _FakePipe:
    def __init__(self) -> None:
        self.zadd_calls: list[tuple[str, dict]] = []
        self.executed = False

    def zadd(self, key, mapping):
        self.zadd_calls.append((key, mapping))
        return self

    def zremrangebyscore(self, *a, **k):
        return self

    def zremrangebyrank(self, *a, **k):
        return self

    def expire(self, *a, **k):
        return self

    async def execute(self):
        self.executed = True
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.pipe = _FakePipe()

    def pipeline(self, transaction=True):
        return self.pipe


@pytest.fixture(autouse=True)
def _clear_sig():
    d._MARCH_TRACE_SIG.clear()
    yield
    d._MARCH_TRACE_SIG.clear()


def _enqueued_dispatch() -> d.MarchDispatch:
    return d.MarchDispatch(
        enqueued=(d.MarchEnqueue("intel", "intel_run", "march:0", 80_060),),
        skipped=(),
    )


async def test_write_march_trace_records_enqueued_decision():
    redis = _FakeRedis()
    await d.write_march_trace(
        redis, "42", _enqueued_dispatch(),
        idle_slots=2, stamina=120.0, had_candidates=True, now=NOW,
    )
    assert redis.pipe.executed
    key, mapping = redis.pipe.zadd_calls[0]
    assert key == "wos:player:42:march_decisions"
    member, score = next(iter(mapping.items()))
    assert score == NOW
    payload = json.loads(member)
    assert payload["action"] == "dispatch"
    assert payload["target"] == "intel"
    assert payload["idle_slots"] == 2
    assert payload["enqueued"][0]["domain"] == "intel"
    assert "queued intel" in payload["reason"]


async def test_write_march_trace_idle_reason_no_slots():
    redis = _FakeRedis()
    empty = d.MarchDispatch(enqueued=(), skipped=())
    await d.write_march_trace(
        redis, "42", empty,
        idle_slots=0, stamina=None, had_candidates=False, now=NOW,
    )
    payload = json.loads(next(iter(redis.pipe.zadd_calls[0][1].items()))[0])
    assert payload["action"] == "idle"
    assert "слот" in payload["reason"]  # "нет свободных march-слотов"


async def test_write_march_trace_signature_gates_unchanged_outcome():
    redis = _FakeRedis()
    empty = d.MarchDispatch(enqueued=(), skipped=())
    await d.write_march_trace(
        redis, "42", empty, idle_slots=0, stamina=None, had_candidates=False, now=NOW,
    )
    # Same outcome on the next tick → gated, no second write.
    await d.write_march_trace(
        redis, "42", empty, idle_slots=0, stamina=None, had_candidates=False, now=NOW + 30,
    )
    assert len(redis.pipe.zadd_calls) == 1

    # A different outcome → writes again.
    await d.write_march_trace(
        redis, "42", _enqueued_dispatch(), idle_slots=2, stamina=80.0,
        had_candidates=True, now=NOW + 60,
    )
    assert len(redis.pipe.zadd_calls) == 2
