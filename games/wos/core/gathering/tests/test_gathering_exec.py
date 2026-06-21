"""Unit tests for the gathering scarcest-resource picker (pure logic)."""
from __future__ import annotations

import pytest
from games.wos.core.gathering.exec import (
    DSL_EXEC_HANDLERS,
    _parse_amount,
    _pick_scarcest,
    _resource_balances,
)

from navigation import screen_graph


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("479", 479.0),
        ("74,749,653", 74_749_653.0),
        ("74.7M", 74_700_000.0),
        ("2.24M", 2_240_000.0),
        ("812K", 812_000.0),
        ("1.2B", 1_200_000_000.0),
        ("  6,043 ", 6043.0),
        ("3.5m", 3_500_000.0),
    ],
)
def test_parse_amount(text, expected):
    assert _parse_amount(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, "", "—", "n/a", "Meat"])
def test_parse_amount_unparseable(text):
    assert _parse_amount(text) is None


def test_pick_scarcest_lowest_wins():
    amounts = {"meat": 74.7e6, "wood": 12e6, "coal": 2.24e6, "iron": 6e6}
    assert _pick_scarcest(amounts) == "coal"


def test_pick_scarcest_skips_none():
    amounts = {"meat": None, "wood": 5e6, "coal": None, "iron": 9e6}
    assert _pick_scarcest(amounts) == "wood"


def test_pick_scarcest_all_none():
    assert _pick_scarcest({"meat": None, "wood": None}) is None


def test_pick_scarcest_tie_breaks_to_first():
    # Equal amounts → earliest insertion order wins (meat before iron).
    amounts = {"meat": 5e6, "wood": 9e6, "coal": 9e6, "iron": 5e6}
    assert _pick_scarcest(amounts) == "meat"


def test_resource_balances_parses_to_ints():
    out = _resource_balances(
        {"meat": "74.7M", "wood": "812K", "coal": "1.2B", "iron": "74,749,653"}
    )
    assert out == {"meat": 74_700_000, "wood": 812_000, "coal": 1_200_000_000, "iron": 74_749_653}


def test_resource_balances_skips_unparseable():
    # A misread is skipped (not written as zero) so it can't clobber a good value.
    out = _resource_balances({"meat": "5M", "wood": "", "coal": "--", "iron": None})
    assert out == {"meat": 5_000_000}


def test_record_resources_registered():
    assert "record_resources" in DSL_EXEC_HANDLERS


def test_main_city_routes_to_resource_overview():
    assert screen_graph.route_taps("main_city", "gathering.resources", game="wos") == [
        ["main_city.resources"]
    ]
    assert screen_graph.route_taps("gathering.resources", "main_city", game="wos") == [
        ["icon.page.back"]
    ]
    assert "gathering.resources" in screen_graph.screen_verify_screen_names()
