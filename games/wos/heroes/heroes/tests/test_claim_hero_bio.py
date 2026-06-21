"""Coverage for the ``claim_hero_bio`` diamond-claim flow.

A red dot on a hero card's book/wiki icon means an unclaimed biography reward.
The analyzer rule ``heroes.unit.bio.scan`` pushes ``claim_hero_bio``, which opens
the Story and taps the chest (verified on-device: 50 diamonds granted, the book
red dot cleared). This guards the scenario shape, the chest region + crop, the
``has_red_dot`` flag on the wiki region, and the analyzer push rule.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from dsl import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[5]
MODULE_DIR = Path(__file__).resolve().parents[1]


def _area() -> dict:
    return yaml.safe_load((MODULE_DIR / "area.yaml").read_text(encoding="utf-8"))


def _region(name: str) -> dict | None:
    found: dict | None = None

    def walk(o: object) -> None:
        nonlocal found
        if isinstance(o, dict):
            if o.get("name") == name and "bbox" in o:
                found = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(_area())
    return found


def test_resolves_literal_key() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "claim_hero_bio")
    assert resolved is not None
    assert resolved.path.name == "claim_hero_bio.yaml"


def test_scenario_shape() -> None:
    _path, doc = _tmpl.load_doc(REPO_ROOT, "claim_hero_bio")
    assert doc["enabled"] is True
    gate = doc["steps"][0]
    # Gated on the book/wiki red dot.
    assert gate["match"] == "page.heroes.unit.wiki"
    assert gate["isRedDot"] is True
    inner = gate["steps"]
    assert inner[0]["click"] == "page.heroes.unit.wiki"
    claim = next(s for s in inner if s.get("while_match") == "page.heroes.unit.bio.chest")
    assert claim["steps"][0]["click"] == "page.heroes.unit.bio.chest"
    assert any(s.get("system_back") for s in inner)


def test_chest_region_and_crop() -> None:
    assert _region("page.heroes.unit.bio.chest") is not None
    crop = (
        MODULE_DIR
        / "references"
        / "crop"
        / "page.heroes.unit_page.heroes.unit.bio.chest.png"
    )
    assert crop.is_file()


def test_wiki_region_has_red_dot() -> None:
    wiki = _region("page.heroes.unit.wiki")
    assert wiki is not None
    assert wiki.get("has_red_dot") is True


def test_analyzer_pushes_claim() -> None:
    doc = yaml.safe_load((MODULE_DIR / "analyze" / "analyze.yaml").read_text())
    rule = next(r for r in doc["overlay"] if r["name"] == "heroes.unit.bio.scan")
    assert rule["region"] == "page.heroes.unit.wiki"
    assert rule["isRedDot"] is True
    assert rule["screens"] == ["page.heroes.unit"]
    assert rule["steps"][0]["push_scenario"] == "claim_hero_bio"
