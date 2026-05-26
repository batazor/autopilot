"""Parse `player.power` OCR text → int.

OCR on the chief-profile power label returns the in-game power digit string with
thousands separators (commas, spaces, NBSP) and stray punctuation. `parse_ocr_integer`
must collapse them deterministically — the sync scenario relies on this
to populate `gamer.power` in SQLite (consumed by `/player-stats`).
"""
from __future__ import annotations

import pytest

from tasks.dsl_ocr_mixin import parse_ocr_integer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234567", 1234567),
        ("1,234,567", 1234567),
        ("1 234 567", 1234567),
        ("1 234 567", 1234567),  # NBSP thousands separator
        ("12,345", 12345),
        ("0", 0),
        ("  42  ", 42),
        ("Power: 9,876,543", 9876543),
        ("3.5", 35),  # decimal collapses — game UI never shows fractional power
    ],
)
def test_parse_ocr_integer_strips_separators(text: str, expected: int) -> None:
    assert parse_ocr_integer(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "abc",
        "—",
        "no digits here",
    ],
)
def test_parse_ocr_integer_returns_none_when_no_digits(text: str) -> None:
    assert parse_ocr_integer(text) is None


def test_parse_ocr_integer_handles_none_input() -> None:
    assert parse_ocr_integer(None) is None  # type: ignore[arg-type]
