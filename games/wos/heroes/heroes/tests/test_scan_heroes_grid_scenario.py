"""Coverage for the ``scan_heroes_grid`` roster-scan scenario.

The scenario populates ``state.heroes.entries`` by NCC-matching the grid against
the 62 wiki icons, then paging down to cover larger rosters. Guards the literal
resolution and the scan→swipe→scan shape so the scroll loop can't silently drop.
"""
from __future__ import annotations

from pathlib import Path

from dsl import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_resolves_literal_key() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "scan_heroes_grid")
    assert resolved is not None
    assert resolved.path.name == "scan_heroes_grid.yaml"


def test_scan_then_scroll_shape() -> None:
    _path, doc = _tmpl.load_doc(REPO_ROOT, "scan_heroes_grid")
    assert doc["enabled"] is True
    assert doc["node"] == "heroes"
    steps = doc["steps"]
    # First a top-screen scan, then a paging loop that swipes + re-scans.
    assert steps[0]["exec"] == "scan_heroes_grid"
    loop = next(s for s in steps if "loop" in s)["loop"]
    assert loop["max"] >= 1
    inner = loop["steps"]
    assert inner[0]["swipe_direction"]["direction"] == "up"
    assert any(s.get("exec") == "scan_heroes_grid" for s in inner)
