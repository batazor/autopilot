"""Cross-domain /planner/overview — grand totals = sum of the per-domain roadmaps."""
from __future__ import annotations

from src.api.routers.planner import CharmsBody, GearBody, OverviewBody, _overview


def test_overview_sums_material_domains():
    out = _overview(OverviewBody(
        charms=CharmsBody(owned={}, target_level=2),   # 18 slots → L2
        gear=GearBody(owned={}, target_level=2),       # 6 pieces → green_1
    ))
    assert set(out["domains"]) == {"charms", "gear"}
    assert out["totals"]["charm_guide"] == 18 * (5 + 40)
    assert out["totals"]["hardened_alloy"] == 6 * (1500 + 3800)

    # the grand total is exactly the sum of the per-domain roadmap costs.
    summed: dict[str, int] = {}
    for d in out["domains"].values():
        for res, amt in d["cost"].items():
            summed[res] = summed.get(res, 0) + amt
    assert summed == out["totals"]


def test_overview_empty_is_empty():
    out = _overview(OverviewBody())
    assert out == {"domains": {}, "totals": {}}
