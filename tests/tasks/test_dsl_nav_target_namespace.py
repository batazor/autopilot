"""Pre-flight nav target must honour a shared helper's region namespace.

Regression: ``tabs.strip.advance`` is a single cross-namespace helper with
``nodes: [shop, …, deals, …]``. The executor used to navigate to
``allowed_nodes[0]`` (the Shop hub) whenever the bot was momentarily off any
listed node — even for a Deals-region push (``args.region = deals.tabs_strip``).
The bot would then walk into Shop and no-op there (the exec handler's
region/current_screen namespace guard rejects the cross-namespace tap),
stranding it on a Shop page with no pending work.

``_select_nav_target`` makes the target follow the region arg's namespace.
"""
from __future__ import annotations

from tasks.dsl_scenario_execute_mixin import _select_nav_target

# Mirrors games/wos/core/common/scenarios/tabs.strip.advance.yaml ordering:
# Shop nodes first, Deals nodes after.
_TABS_STRIP_NODES = (
    "shop",
    "shop.daily_deals",
    "deals",
    "deals.sign_in",
    "deals.hall_of_heroes",
)


def test_deals_region_routes_to_deals_not_first_shop_node() -> None:
    target = _select_nav_target(
        _TABS_STRIP_NODES, {"region": "deals.tabs_strip", "next_region": "deals.next.left"}
    )
    assert target == "deals"


def test_shop_region_routes_to_shop() -> None:
    target = _select_nav_target(
        _TABS_STRIP_NODES, {"region": "shop.tabs_strip", "next_region": "shop.tab.next_page"}
    )
    assert target == "shop"


def test_no_region_arg_falls_back_to_first_node() -> None:
    assert _select_nav_target(_TABS_STRIP_NODES, None) == "shop"
    assert _select_nav_target(_TABS_STRIP_NODES, {}) == "shop"


def test_next_region_used_when_region_absent() -> None:
    target = _select_nav_target(_TABS_STRIP_NODES, {"next_region": "deals.next.left"})
    assert target == "deals"


def test_unknown_namespace_falls_back_to_first_node() -> None:
    # Region namespace not present among allowed nodes → historical default.
    target = _select_nav_target(_TABS_STRIP_NODES, {"region": "mail.tabs_strip"})
    assert target == "shop"


def test_empty_nodes_returns_empty() -> None:
    assert _select_nav_target((), {"region": "deals.tabs_strip"}) == ""


def test_single_namespace_scenario_unaffected() -> None:
    # A region without a dotted namespace can't match; first node still wins.
    assert _select_nav_target(("deals", "deals.bank"), {"region": "tabs_strip"}) == "deals"
