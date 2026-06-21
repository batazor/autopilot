"""Pure model (de)serialization tests (no Redis)."""
from __future__ import annotations

from coord import keys
from coord.models import (
    STATUS_RUNNING,
    BarrierSpec,
    Directive,
    DirectiveStatus,
    DirectiveTarget,
    InstanceSnapshot,
)


def test_directive_json_round_trip():
    d = Directive(
        directive_id="d1",
        kind="enqueue_scenario",
        target=DirectiveTarget.fid("2247253"),
        payload={"scenario": "event.gather", "player_id": "2247253"},
        source="orchestrator",
        created_at=12.5,
        ttl_s=600.0,
        idempotency_key="run1:0:2247253:run_scenario",
    )
    back = Directive.from_json(d.to_json())
    assert back == d
    assert back.dedup_key() == "run1:0:2247253:run_scenario"


def test_directive_dedup_key_falls_back_to_id():
    d = Directive(directive_id="d2", kind="ping", target=DirectiveTarget.all_())
    assert d.dedup_key() == "d2"


def test_directive_from_bytes():
    d = Directive(directive_id="d3", kind="ping", target=DirectiveTarget.instance("dev-a"))
    assert Directive.from_json(d.to_json().encode()) == d


def test_instance_snapshot_online_fresh():
    snap = InstanceSnapshot.from_hash(
        "dev-a",
        {
            keys.FIELD_ACTIVE_PLAYER: "111",
            keys.FIELD_ALLIANCE_TAG: "WOLF",
            keys.FIELD_GAME: "wos",
            keys.FIELD_COORD_SEEN_AT: "1000.0",
            keys.FIELD_MARCH_SLOTS_FREE: "3",
            keys.FIELD_MARCH_SLOTS_TOTAL: "5",
            keys.FIELD_PAUSED: "0",
        },
        now=1005.0,
    )
    assert snap.online is True
    assert snap.active_player == "111"
    assert snap.alliance_tag == "WOLF"
    assert snap.march_slots_free == 3
    assert snap.march_slots_total == 5
    assert snap.paused is False


def test_instance_snapshot_offline_stale():
    snap = InstanceSnapshot.from_hash(
        "dev-a",
        {keys.FIELD_COORD_SEEN_AT: "1000.0"},
        now=1000.0 + keys.FLEET_STALE_AFTER_S + 1.0,
    )
    assert snap.online is False
    assert snap.march_slots_free is None  # unobserved → None, not 0


def test_instance_snapshot_bytes_hash():
    snap = InstanceSnapshot.from_hash(
        "dev-a",
        {keys.FIELD_ACTIVE_PLAYER: b"111", keys.FIELD_COORD_SEEN_AT: b"1000.0"},
        now=1001.0,
    )
    assert snap.active_player == "111"
    assert snap.online is True


def test_barrier_spec_round_trip():
    spec = BarrierSpec(barrier_id="b1", required_n=2, deadline_ts=123.0, group="raid")
    assert BarrierSpec.from_json(spec.to_json()) == spec


def test_directive_status_from_hash():
    st = DirectiveStatus.from_hash(
        "d1",
        {"instance_id": "dev-a", "state": STATUS_RUNNING, "started_at": "5.0"},
    )
    assert st.state == STATUS_RUNNING
    assert st.instance_id == "dev-a"
    assert st.started_at == 5.0
