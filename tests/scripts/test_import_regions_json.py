"""The zones importer updates a module's area file from a label-editor export.

Contract (mirrors the docstring of scripts/import_regions_json.py):
* existing region names get ONLY their bbox replaced — action/threshold/
  preprocess/_comment keys survive, so re-labeling a drifted zone is safe;
* unknown names are appended to the payload's screen with exist/0.9 defaults;
* the file is rewritten as JSON text — the dashboard labeling save's
  canonical area format (yaml.safe_load still reads it: JSON ⊂ YAML);
* nothing is deleted.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(monkeypatch, argv: list[str]) -> None:
    spec = importlib.util.spec_from_file_location(
        "import_regions_json_test", _REPO_ROOT / "scripts" / "import_regions_json.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["import_regions_json.py", *argv])
    module.main()


def _module_with_area(tmp_path: Path, *, as_json: bool) -> tuple[Path, Path]:
    module_dir = tmp_path / "games" / "wos" / "demo"
    module_dir.mkdir(parents=True)
    doc: dict[str, Any] = {
        "version": 2,
        "screens": [
            {
                "id": 7,
                "screen_id": "demo",
                "ocr": "references/demo.png",
                "regions": [
                    {
                        "name": "demo.title",
                        "_comment": "kept through the import",
                        "action": "text",
                        "type": "string",
                        "preprocess": "title_line",
                        "threshold": 0.7,
                        "bbox": {
                            "x": 10.0,
                            "y": 1.0,
                            "width": 30.0,
                            "height": 4.0,
                            "rotation": 0,
                            "original_width": 1080,
                            "original_height": 1920,
                        },
                    }
                ],
            }
        ],
    }
    area_path = module_dir / "area.yaml"
    if as_json:
        area_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    else:
        area_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return module_dir, area_path


def _export(tmp_path: Path, *, screen: str = "demo") -> Path:
    payload = {
        "type": "regions",
        "screen": screen,
        "image": "shot.png",
        "regions": [
            {"name": "demo.title", "bbox": [12.5, 2.0, 28.0, 5.0]},
            {"name": "demo.claim", "bbox": [30.0, 80.0, 40.0, 8.0]},
        ],
    }
    p = tmp_path / "export.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_updates_bbox_in_place_and_appends_new(tmp_path, monkeypatch) -> None:
    module_dir, area_path = _module_with_area(tmp_path, as_json=True)
    export = _export(tmp_path)

    _run(monkeypatch, [str(export), "--module", str(module_dir)])

    text = area_path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("{"), "canonical area format is JSON text"
    doc = json.loads(text)
    regions = doc["screens"][0]["regions"]
    by_name = {r["name"]: r for r in regions}

    title = by_name["demo.title"]
    # bbox replaced…
    assert title["bbox"]["x"] == 12.5
    assert title["bbox"]["width"] == 28.0
    # …the capture resolution of the original labeling is carried over…
    assert title["bbox"]["original_width"] == 1080
    # …and every other key survives.
    assert title["action"] == "text"
    assert title["preprocess"] == "title_line"
    assert title["_comment"] == "kept through the import"

    claim = by_name["demo.claim"]
    assert claim["action"] == "exist"
    assert claim["threshold"] == 0.9
    assert claim["bbox"]["y"] == 80.0
    assert claim["bbox"]["original_width"] == 720


def test_yaml_styled_file_is_readable_after_import(tmp_path, monkeypatch) -> None:
    """A hand-written YAML area file is rewritten as JSON text (the dashboard
    convention) — and yaml.safe_load still reads it, so consumers don't care."""
    module_dir, area_path = _module_with_area(tmp_path, as_json=False)
    export = _export(tmp_path)

    _run(monkeypatch, [str(export), "--module", str(module_dir)])

    doc = yaml.safe_load(area_path.read_text(encoding="utf-8"))
    names = [r["name"] for r in doc["screens"][0]["regions"]]
    assert names == ["demo.title", "demo.claim"]


def test_new_screen_entry_created_for_unknown_screen(tmp_path, monkeypatch) -> None:
    module_dir, area_path = _module_with_area(tmp_path, as_json=True)
    export = _export(tmp_path, screen="demo.other")
    # Make both regions new so they land on the new screen.
    payload = json.loads(export.read_text(encoding="utf-8"))
    payload["regions"] = [{"name": "demo.other.button", "bbox": [1, 2, 3, 4]}]
    export.write_text(json.dumps(payload), encoding="utf-8")

    _run(monkeypatch, [str(export), "--module", str(module_dir)])

    doc = yaml.safe_load(area_path.read_text(encoding="utf-8"))
    entry = next(e for e in doc["screens"] if e["screen_id"] == "demo.other")
    assert entry["id"] == 8  # max existing id + 1
    assert entry["ocr"] == "references/shot.png"
    assert entry["regions"][0]["name"] == "demo.other.button"


def test_dry_run_leaves_file_untouched(tmp_path, monkeypatch) -> None:
    module_dir, area_path = _module_with_area(tmp_path, as_json=True)
    before = area_path.read_text(encoding="utf-8")
    export = _export(tmp_path)

    _run(monkeypatch, [str(export), "--module", str(module_dir), "--dry-run"])

    assert area_path.read_text(encoding="utf-8") == before


def test_rejects_scene_exports(tmp_path, monkeypatch) -> None:
    module_dir, _ = _module_with_area(tmp_path, as_json=True)
    bad = tmp_path / "scene.json"
    bad.write_text(json.dumps({"type": "dreamscape_scene", "regions": []}), encoding="utf-8")

    import pytest

    with pytest.raises(SystemExit):
        _run(monkeypatch, [str(bad), "--module", str(module_dir)])
