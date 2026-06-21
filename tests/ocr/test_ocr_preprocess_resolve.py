"""Resolution of ``preprocess`` tag from explicit vs. ``type``-derived defaults."""

from __future__ import annotations

import pytest

from ocr.preprocess import resolve_preprocess


@pytest.mark.parametrize("type_hint", ["time", "TIME", "  time  "])
def test_auto_derives_fast_line_for_time(type_hint: str) -> None:
    assert resolve_preprocess(None, type_hint) == "fast_line"


@pytest.mark.parametrize(
    "type_hint",
    ["int", "integer", "Int", "  integer  "],
)
def test_auto_derives_fast_digits_for_integer_types(type_hint: str) -> None:
    # Integer regions get the digit whitelist (PSM 7 + 0-9 only) so an
    # ambiguous glyph can't be emitted as a symbol and shorten the number.
    assert resolve_preprocess(None, type_hint) == "fast_digits"


@pytest.mark.parametrize(
    "type_hint",
    ["string", "", None, "float", "bool"],
)
def test_no_auto_for_non_digit_types(type_hint: str | None) -> None:
    assert resolve_preprocess(None, type_hint) is None


def test_explicit_value_overrides_auto() -> None:
    assert resolve_preprocess("enhance", "time") == "enhance"
    assert resolve_preprocess("fast_line", "integer") == "fast_line"


def test_explicit_value_lowercased() -> None:
    assert resolve_preprocess("ENHANCE", "string") == "enhance"
    assert resolve_preprocess("DIGITS", None) == "digits"
    assert resolve_preprocess("Fast_Line", None) == "fast_line"


def test_empty_explicit_falls_through_to_type() -> None:
    assert resolve_preprocess("", "time") == "fast_line"
    assert resolve_preprocess("   ", "integer") == "fast_digits"
    assert resolve_preprocess(None, "int") == "fast_digits"
    assert resolve_preprocess("fast_line", "integer") == "fast_line"
    assert resolve_preprocess("digits", "integer") == "digits"


def test_unknown_explicit_value_passes_through() -> None:
    assert resolve_preprocess("some_new_pipeline", None) == "some_new_pipeline"
    assert resolve_preprocess("some_new_pipeline", "time") == "some_new_pipeline"
