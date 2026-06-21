"""Coverage for the ``star_tier_up_{hero}`` upgrade-dispatch scenario.

The scenario is the optimizer's ``star_tier_up`` dispatch target — it opens the
hero card's star-promotion panel and taps Ascend. The control flow + region
contract were labelled and verified on-device (Heroes ▸ unit card → Promotion
Preview → Ascend → Obtain-more popup on insufficient shards). This test guards:

* the template resolves + renders for a real hero,
* the step shape (open star panel → gate on Ascend → handle the shard popup),
* every region the steps tap exists in the merged area doc with a crop tile.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from dsl import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[5]
MODULE_DIR = Path(__file__).resolve().parents[1]

_REGIONS = (
    "page.heroes.unit.star",
    "page.heroes.unit.ascend",
    "page.heroes.unit.obtain_more.close",
)


def test_resolves_known_hero() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "star_tier_up_molly")
    assert resolved is not None
    assert resolved.path.name == "star_tier_up_{hero}.yaml"
    assert resolved.context == {"hero_id": "molly", "hero_name": "Molly"}


def test_rejects_unknown_hero() -> None:
    assert _tmpl.resolve(REPO_ROOT, "star_tier_up_not_a_hero") is None


def test_load_doc_renders_node_and_name() -> None:
    loaded = _tmpl.load_doc(REPO_ROOT, "star_tier_up_bahiti")
    assert loaded is not None
    _path, doc = loaded
    assert doc["node"] == "page.heroes.bahiti"
    assert "Bahiti" in doc["name"]


def test_step_shape() -> None:
    _path, doc = _tmpl.load_doc(REPO_ROOT, "star_tier_up_molly")
    steps = doc["steps"]
    # Opens the promotion panel by tapping the star row.
    assert steps[0]["click"] == "page.heroes.unit.star"
    # Gates the action on the Ascend button being present (panel open).
    gate = next(s for s in steps if s.get("while_match") == "page.heroes.unit.ascend")
    inner = gate["steps"]
    assert inner[0]["click"] == "page.heroes.unit.ascend"
    # Insufficient shards → the Obtain-more popup is dismissed.
    assert any(
        s.get("while_match") == "page.heroes.unit.obtain_more.close" for s in inner
    )


def _area_region_names() -> set[str]:
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
    return names


def test_regions_and_crops_present() -> None:
    names = _area_region_names()
    crop_dir = MODULE_DIR / "references" / "crop"
    for region in _REGIONS:
        assert region in names, f"missing area region: {region}"
        crop = crop_dir / f"page.heroes.unit_{region}.png"
        assert crop.is_file(), f"missing crop tile: {crop.name}"
