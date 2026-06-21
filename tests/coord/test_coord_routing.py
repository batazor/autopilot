"""Pure directive-routing tests (no Redis)."""
from __future__ import annotations

from coord.models import DirectiveTarget, FleetView, InstanceSnapshot
from coord.routing import resolve_targets


def _fleet():
    return FleetView(
        instances=(
            InstanceSnapshot("dev-a", active_player="111", alliance_tag="WOLF", online=True),
            InstanceSnapshot("dev-b", active_player="222", alliance_tag="WOLF", online=True),
            InstanceSnapshot("dev-c", active_player="333", alliance_tag="BEAR", online=True),
            # offline: hosts fid 444 but heartbeat is stale
            InstanceSnapshot("dev-d", active_player="444", alliance_tag="WOLF", online=False),
        )
    )


def test_target_instance_returns_value_even_if_offline():
    assert resolve_targets(DirectiveTarget.instance("dev-d"), _fleet()) == ["dev-d"]


def test_target_instance_empty_value():
    assert resolve_targets(DirectiveTarget.instance(""), _fleet()) == []


def test_target_fid_resolves_to_online_host():
    assert resolve_targets(DirectiveTarget.fid("222"), _fleet()) == ["dev-b"]


def test_target_fid_offline_host_unresolved():
    # 444 is only on the offline dev-d → not routable (caller defers / switches).
    assert resolve_targets(DirectiveTarget.fid("444"), _fleet()) == []


def test_target_fid_unknown():
    assert resolve_targets(DirectiveTarget.fid("999"), _fleet()) == []


def test_target_all_is_online_only():
    got = resolve_targets(DirectiveTarget.all_(), _fleet())
    assert set(got) == {"dev-a", "dev-b", "dev-c"}
    assert "dev-d" not in got


def test_target_alliance_matches_tag_and_online():
    got = resolve_targets(DirectiveTarget.alliance("WOLF"), _fleet())
    # dev-a, dev-b are online WOLF; dev-d is WOLF but offline → excluded.
    assert set(got) == {"dev-a", "dev-b"}


def test_target_unknown_kind():
    assert resolve_targets(DirectiveTarget("bogus", "x"), _fleet()) == []
