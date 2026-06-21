"""Tests for the Bear Hunt cooldown-line parser (pure)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from games.wos.events.bear_hunt.parser import parse_cooldown, parse_level


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # live reads off bs1
        ("On cooldown: 19:40:46", timedelta(hours=19, minutes=40, seconds=46)),
        ("On cooldown: 1d 13:29:45", timedelta(days=1, hours=13, minutes=29, seconds=45)),
        # title_line preprocess renders separators as spaces
        ("On cooldown 19 40 46", timedelta(hours=19, minutes=40, seconds=46)),
        ("On cooldown 1d 04 28 43", timedelta(days=1, hours=4, minutes=28, seconds=43)),
        ("On cooldown: 00:00:30", timedelta(seconds=30)),
    ],
)
def test_parses_cooldown(text, expected):
    assert parse_cooldown(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Enable",
        "Damage Rewards",
        # an active-window countdown is NOT a cooldown — no "cool" word → ignored
        "Active 00:29:59",
        # cooldown word present but no timer
        "On cooldown:",
    ],
)
def test_no_cooldown_returns_none(text):
    assert parse_cooldown(text) is None


def test_out_of_range_rejected():
    assert parse_cooldown("On cooldown: 99:99:99") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Lv. 5", 5),
        ("Lv. 4", 4),
        ("v 5", 5),   # OCR often drops the "L"
        ("v5", 5),
        ("Lv. 12", 12),
        ("", None),
        ("Lv.", None),
    ],
)
def test_parse_level(text, expected):
    assert parse_level(text) == expected
