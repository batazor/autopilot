"""Resolution of ``preprocess`` tag from explicit vs. ``type``-derived defaults.

The auto-derivation half is what makes timer / integer regions cheap by
default; the explicit half lets a problematic region opt out without touching
the backend. Tests pin the small, opinionated rule set so a future "let's
auto-enhance ``type: string`` too" change is a deliberate, reviewable diff.
"""

from __future__ import annotations

import pytest

from ocr.preprocess import resolve_preprocess


@pytest.mark.parametrize(
    "type_hint",
    ["time", "int", "integer", "TIME", "Int", "  integer  "],
)
def test_auto_derives_fast_line_for_digit_types(type_hint: str) -> None:
    """All listed ``type`` values map to ``fast_line`` regardless of case /
    whitespace — the resolver is the single normalization point."""
    assert resolve_preprocess(None, type_hint) == "fast_line"


@pytest.mark.parametrize(
    "type_hint",
    ["string", "", None, "float", "bool"],
)
def test_no_auto_for_non_digit_types(type_hint: str | None) -> None:
    """Types outside the digit set fall through to ``None``: block-style OCR
    runs on the raw crop, no implicit fast_line."""
    assert resolve_preprocess(None, type_hint) is None


def test_explicit_value_overrides_auto() -> None:
    """An explicit ``preprocess: enhance`` on a ``type: time`` region wins —
    the operator opts out of the fast_line default. This is the escape hatch
    for regions where single-line segmentation misreads the line."""
    assert resolve_preprocess("enhance", "time") == "enhance"


def test_explicit_value_lowercased() -> None:
    """Backend dispatches on lowercase tags; resolver normalizes here so
    callers don't have to."""
    assert resolve_preprocess("ENHANCE", "string") == "enhance"
    assert resolve_preprocess("Fast_Line", None) == "fast_line"


def test_empty_explicit_falls_through_to_type() -> None:
    """An explicit empty / whitespace value is treated as "not set" —
    falls through to the ``type``-derived default. YAML's ``preprocess:``
    (no value) parses to ``None``; ``preprocess: ""`` parses to ``""``;
    both should behave identically."""
    assert resolve_preprocess("", "time") == "fast_line"
    assert resolve_preprocess("   ", "integer") == "fast_line"
    assert resolve_preprocess(None, "int") == "fast_line"


def test_unknown_explicit_value_passes_through() -> None:
    """Future preprocess values added to OCR don't require resolver changes —
    anything non-empty just forwards. Wrong values surface in OCR handling, not
    as client-side AttributeError."""
    assert resolve_preprocess("some_new_pipeline", None) == "some_new_pipeline"
    assert resolve_preprocess("some_new_pipeline", "time") == "some_new_pipeline"
