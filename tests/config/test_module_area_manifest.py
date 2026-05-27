from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.crop_paths import exported_crop_png, resolve_reference_path

if TYPE_CHECKING:
    from pathlib import Path


def _seed_module(tmp_path: Path, name: str, area_doc: dict) -> Path:
    module_root = _modules_root_for(_default_game(), repo_root=tmp_path) / name
    module_root.mkdir(parents=True)
    (module_root / "module.yaml").write_text(
        yaml.safe_dump({"id": name, "name": name.upper()}), encoding="utf-8"
    )
    (module_root / "area.yaml").write_text(yaml.safe_dump(area_doc), encoding="utf-8")
    return module_root


def test_load_area_doc_aggregates_module_area_manifests(tmp_path: Path) -> None:
    _seed_module(
        tmp_path,
        "vip",
        {
            "screens": [
                {
                    "id": "vip",
                    "ocr": "references/page.vip.png",
                    "versions": [{"id": "v2", "ocr": "references/page.vip.v2.png"}],
                    "regions": [{"name": "vip.claim", "bbox": {}}],
                }
            ]
        },
    )
    _seed_module(
        tmp_path,
        "mail",
        {
            "screens": [
                {
                    "id": "mail",
                    "ocr": "references/page.mail.png",
                    "regions": [{"name": "mail.claim", "bbox": {}}],
                }
            ]
        },
    )

    doc = load_area_doc(tmp_path)

    screens = doc["screens"]
    by_id = {s["id"]: s for s in screens}
    assert set(by_id) == {"vip", "mail"}
    assert by_id["vip"]["ocr"] == "games/wos/vip/references/page.vip.png"
    assert by_id["vip"]["versions"][0]["ocr"] == "games/wos/vip/references/page.vip.v2.png"


def test_load_area_doc_returns_empty_when_no_modules(tmp_path: Path) -> None:
    doc = load_area_doc(tmp_path)
    assert doc == {"version": 2, "screens": []}


def test_module_area_yaml_drives_region_lookup(tmp_path: Path) -> None:
    _seed_module(
        tmp_path,
        "fixture",
        {
            "screens": [
                {
                    "id": "fixture",
                    "ocr": "references/fixture.png",
                    "regions": [{"name": "fixture.button", "bbox": {}}],
                }
            ]
        },
    )

    doc = load_area_doc(tmp_path)

    assert doc["screens"][0]["id"] == "fixture"
    assert screen_region_by_name(doc, "fixture.button") is not None


def test_module_reference_uses_module_crop_directory(tmp_path: Path) -> None:
    ref_rel = "games/wos/vip/references/page.vip.png"

    crop = exported_crop_png(tmp_path, ref_rel, "vip.claim")
    ref_path = resolve_reference_path(tmp_path, ref_rel)

    assert crop == tmp_path / "games/wos/vip/references/crop/page.vip_vip.claim.png"
    assert ref_path == tmp_path / ref_rel


def test_nested_module_reference_uses_nested_module_crop_directory(tmp_path: Path) -> None:
    ref_rel = "games/wos/events/trials/references/main_city.trials.png"

    crop = exported_crop_png(tmp_path, ref_rel, "module.event.icon")

    assert crop == (
        tmp_path
        / "games/wos/events/trials/references/crop/main_city.trials_module.event.icon.png"
    )


def test_dsl_load_area_json_includes_module_regions() -> None:
    from config.paths import repo_root
    from tasks.dsl_scenario_helpers import _load_area_json

    doc = _load_area_json(repo_root())
    pair = screen_region_by_name(doc, "main_city.to.backpack")
    assert pair is not None
    assert pair[1]["name"] == "main_city.to.backpack"
