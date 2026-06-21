"""IO-layer tests for the coord primitives against a real Redis (testcontainers).

Marked ``integration`` — they skip when Docker is unavailable (see the
``redis_container`` fixture). They exercise the Lua scripts (lease, reverse-index
compare-and-delete, barrier arrive incl. cjson), Streams, and pipelines that a
hand-rolled fake can't faithfully emulate.
"""
from __future__ import annotations

import time

import pytest

from coord import keys
from coord.barrier import Barrier
from coord.bus import DirectiveBus
from coord.fleet import Fleet
from coord.lease import Lease
from coord.models import (
    BARRIER_ABORTED,
    BARRIER_READY,
    BARRIER_TIMED_OUT,
    STATUS_DONE,
    BarrierSpec,
    Directive,
    DirectiveStatus,
    DirectiveTarget,
    FleetView,
    InstanceSnapshot,
)

pytestmark = pytest.mark.integration


# --- Lease --------------------------------------------------------------------
async def test_lease_acquire_release_refresh(redis_async):
    a = Lease("world_boss", redis=redis_async)
    b = Lease("world_boss", redis=redis_async)
    tok = await a.acquire(ttl_s=60)
    assert tok is not None
    # held → second acquirer is blocked
    assert await b.acquire(ttl_s=60) is None
    # wrong token can't release (fenced)
    assert await a.release("not-the-token") is False
    assert await b.acquire(ttl_s=60) is None
    # owner refresh works; non-owner refresh doesn't
    assert await a.refresh(tok, ttl_s=120) is True
    assert await b.refresh("nope", ttl_s=120) is False
    # owner release frees it; now someone else can take it
    assert await a.release(tok) is True
    tok2 = await b.acquire(ttl_s=60)
    assert tok2 is not None and tok2 != tok


# --- Directive bus ------------------------------------------------------------
async def test_bus_post_drain_dedup_status_audit(redis_async):
    bus = DirectiveBus(redis_async)
    view = FleetView((InstanceSnapshot("dev-a", active_player="111", online=True),))
    d = Directive(
        directive_id="d1",
        kind="enqueue_scenario",
        target=DirectiveTarget.fid("111"),
        payload={"scenario": "event.gather"},
        idempotency_key="run1:0:111",
    )
    posted = await bus.post(d, view, now=1000.0)
    assert posted == ["dev-a"]

    drained = await bus.drain("dev-a")
    assert len(drained) == 1
    assert drained[0].directive_id == "d1"
    # inbox is now empty
    assert await bus.drain("dev-a") == []

    # dedup claim: first True, redelivery False
    assert await bus.claim("dev-a", d.dedup_key()) is True
    assert await bus.claim("dev-a", d.dedup_key()) is False

    await bus.set_status(
        DirectiveStatus("d1", "dev-a", STATUS_DONE, result="queued"), now=1001.0
    )
    st = await bus.read_status("d1")
    assert st is not None and st.state == STATUS_DONE and st.result == "queued"

    audit = await bus.read_audit(count=10)
    assert any(e.get("kind") == "posted" and e.get("directive_id") == "d1" for e in audit)


async def test_bus_post_to_offline_fid_is_unrouted(redis_async):
    bus = DirectiveBus(redis_async)
    view = FleetView((InstanceSnapshot("dev-a", active_player="111", online=False),))
    d = Directive("d2", "ping", DirectiveTarget.fid("111"))
    assert await bus.post(d, view, now=1.0) == []
    assert await bus.drain("dev-a") == []


# --- Fleet registry + reverse index ------------------------------------------
async def test_fleet_reverse_index_round_trip_and_reap(redis_async):
    fleet = Fleet(redis_async)
    await fleet.publish_heartbeat("dev-a", active_player="111", now=1000.0)

    view = await fleet.snapshot(["dev-a"], now=1001.0)
    snap = view.get("dev-a")
    assert snap is not None and snap.online is True and snap.active_player == "111"
    assert await fleet.resolve_fid("111", now=1001.0) == ("dev-a", True)

    # simulate a switch away from 111 → 222 (prev_fid is compare-and-deleted)
    await fleet.publish_heartbeat("dev-a", active_player="222", now=1002.0, prev_fid="111")
    assert await fleet.resolve_fid("111", now=1003.0) == (None, False)
    assert await fleet.resolve_fid("222", now=1003.0) == ("dev-a", True)

    # worker goes stale → snapshot offline, reap prunes the reverse-index entry
    stale_now = 1002.0 + keys.FLEET_STALE_AFTER_S + 5.0
    view2 = await fleet.snapshot(["dev-a"], now=stale_now)
    assert view2.get("dev-a").online is False
    reaped = await fleet.reap_stale(now=stale_now)
    assert reaped == 1
    assert await fleet.resolve_fid("222", now=stale_now) == (None, False)


async def test_fleet_scheduler_populates_alliance_and_slots(redis_async):
    fleet = Fleet(redis_async)
    await fleet.publish_heartbeat("dev-a", active_player="111", now=1000.0)
    await fleet.set_march_slots("dev-a", total=5, free=2)
    # alliance_tag is a separate scheduler-side write
    await redis_async.hset(keys.instance_state_key("dev-a"), keys.FIELD_ALLIANCE_TAG, "WOLF")
    snap = (await fleet.snapshot(["dev-a"], now=1001.0)).get("dev-a")
    assert snap.march_slots_total == 5
    assert snap.march_slots_free == 2
    assert snap.alliance_tag == "WOLF"


# --- Barrier ------------------------------------------------------------------
async def test_barrier_quorum_ready(redis_async):
    now = time.time()
    b = Barrier("bx", redis=redis_async)
    await b.create(BarrierSpec("bx", required_n=2, deadline_ts=now + 100), now=now)
    assert await b.arrive("farm", now=now) != BARRIER_READY
    assert await b.arrive("fighter", now=now) == BARRIER_READY
    assert await b.poll(now=now) == BARRIER_READY
    # ready is sticky even past the deadline
    assert await b.poll(now=now + 1000) == BARRIER_READY


async def test_barrier_flag_set_single_party(redis_async):
    now = time.time()
    b = Barrier("city_empty:run1", redis=redis_async)
    await b.create(BarrierSpec("city_empty:run1", required_n=1, deadline_ts=now + 60), now=now)
    # farm signals "city empty" → the fighter's gate opens
    assert await b.arrive("farm", now=now, note="city_empty") == BARRIER_READY


async def test_barrier_timeout_and_wait(redis_async):
    now = time.time()
    b = Barrier("bt", redis=redis_async)
    # deadline already in the past → poll is TIMED_OUT, wait returns fast
    await b.create(BarrierSpec("bt", required_n=2, deadline_ts=now - 1), now=now)
    await b.arrive("a", now=now)
    assert await b.poll(now=now) == BARRIER_TIMED_OUT
    assert await b.wait(timeout_s=2.0, poll_interval_s=0.05) == BARRIER_TIMED_OUT


async def test_barrier_abort(redis_async):
    now = time.time()
    b = Barrier("ba", redis=redis_async)
    await b.create(BarrierSpec("ba", required_n=2, deadline_ts=now + 60), now=now)
    await b.abort(reason="fighter changed mind")
    assert await b.poll(now=now) == BARRIER_ABORTED
    # arriving on an aborted barrier stays aborted
    assert await b.arrive("farm", now=now) == BARRIER_ABORTED
