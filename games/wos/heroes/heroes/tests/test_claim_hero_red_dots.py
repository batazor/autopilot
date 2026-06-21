"""Coverage for the ``claim_hero_red_dots`` walk scenario.

Opening a hero the first time clears its grid red dot and grants the one-time
diamond reward. The scenario opens the first grid hero, then steps through every
hero via the card's next-unit arrow (verified on-device: Molly → Bahiti → … →
Charlie). This test guards the literal-key resolution, the walk shape, and that
the two regions it taps exist in the merged area doc.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from dsl import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[5]
MODULE_DIR = Path(__file__).resolve().parents[1]


def test_resolves_literal_key() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "claim_hero_red_dots")
    assert resolved is not None
    assert resolved.path.name == "claim_hero_red_dots.yaml"
    assert resolved.context == {}


def test_walk_shape() -> None:
    _path, doc = _tmpl.load_doc(REPO_ROOT, "claim_hero_red_dots")
    assert doc["enabled"] is True
    assert doc["node"] == "heroes"
    steps = doc["steps"]
    # Opens the first hero, then loops the next-unit arrow over the roster.
    assert steps[0]["click"] == "heroes.grid.r0c0"
    walk = next(s for s in steps if s.get("while_match") == "page.heroes.unit.next_unit")
    assert walk["max"] >= 63  # caps above the hero roster size
    assert walk["steps"][0]["click"] == "page.heroes.unit.next_unit"


def test_regions_exist() -> None:
    doc = yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))
    names: set[str] = set()

    def walk(o: object) -> None:
        if isinstance(o, dict):
            if "name" in o and "bbox" in o:
                names.add(str(o["name"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(doc)
    assert "heroes.grid.r0c0" in names
    assert "page.heroes.unit.next_unit" in names
