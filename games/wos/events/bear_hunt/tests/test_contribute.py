"""Tests for the Trap Enhancement MAX detection + target selection (pure)."""
from __future__ import annotations

import pytest
from games.wos.events.bear_hunt.contribute import is_maxed, select_targets


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (5, True),   # MAX_LEVEL
        (6, True),   # at/above cap
        (4, False),
        (1, False),
        (None, False),  # unreadable ≠ maxed → still eligible
    ],
)
def test_is_maxed(level, expected):
    assert is_maxed(level) is expected


@pytest.mark.parametrize(
    ("maxed", "expected"),
    [
        # both have room → pour into both
        ({"1": False, "2": False}, ["1", "2"]),
        # one maxed → only the non-maxed one
        ({"1": True, "2": False}, ["2"]),
        ({"1": False, "2": True}, ["1"]),
        # both maxed → pour into any one (the first)
        ({"1": True, "2": True}, ["1"]),
        ({}, []),
    ],
)
def test_select_targets(maxed, expected):
    assert select_targets(maxed) == expected
