"""Templating + slug unit tests (pure)."""
from __future__ import annotations

from modules.broadcast.templating import render, slug


def test_slug_matches_calendar_semantics() -> None:
    assert slug("Foundry Battle") == "foundry_battle"
    assert slug("Bear Hunt!") == "bear_hunt"
    assert slug("  ") == ""


def test_render_substitutes_known_keys() -> None:
    out = render(
        "{event} starts in {in_hours}h — {alliance} on state {state}!",
        {"event": "Bear Hunt", "in_hours": 2.0, "alliance": "ABC", "state": "42"},
    )
    assert out == "Bear Hunt starts in 2h — ABC on state 42!"


def test_render_trims_float_and_keeps_decimals() -> None:
    assert render("{x}", {"x": 2.0}) == "2"
    assert render("{x}", {"x": 1.5}) == "1.5"


def test_render_leaves_unknown_placeholders() -> None:
    # A typo'd placeholder stays visible instead of silently vanishing.
    assert render("hi {nope}", {"event": "X"}) == "hi {nope}"


def test_render_handles_empty() -> None:
    assert render("", {"a": 1}) == ""
    assert render("no placeholders", {}) == "no placeholders"
