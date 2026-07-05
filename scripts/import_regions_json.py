#!/usr/bin/env python3
"""Import a community-labeled ZONES (regions) JSON into a module's area file.

The JSON comes from the docs-site online label editor
(https://batazor.github.io/autopilot-page/authoring/label-editor/) in the
"Zones (screen regions)" mode — a user drops a screenshot, draws named boxes,
and exports:

    {
      "type": "regions",
      "screen": "hero_detail",
      "image": "shot.png",
      "regions": [{"name": "hero.detail.title", "bbox": [x, y, w, h]}, ...]
    }

``bbox`` values are 0–100 percentages of the frame (x, y, width, height).

This tool UPDATES the target module's area file in place:

* a region whose ``name`` already exists anywhere in the doc gets its bbox
  replaced — every other key (action, threshold, preprocess, comments) is
  preserved, so re-labeling a drifted zone is a one-liner;
* unknown names are appended to the payload's screen entry with the most
  common defaults (``action: exist``, ``threshold: 0.9``) — adjust by hand if
  the region is OCR/colour;
* nothing is ever deleted.

    uv run python scripts/import_regions_json.py export.json \
        --module games/wos/intel [--screen intel] [--dry-run]

The file is rewritten as JSON text (indent 2) — the same canonical format the
dashboard labeling save produces for area.yaml. NB: real ``#`` comments in a
hand-written YAML file do not survive that rewrite — use ``_comment`` keys
(the JSON-styled convention), which do.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dashboard.reference_area_sync import _load_area_file  # noqa: E402

_AREA_BASENAMES = ("area.yaml", "area.yml", "area.json")


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _validate(data: dict) -> tuple[str, list[dict[str, Any]]]:
    if data.get("type") != "regions":
        _fail(
            f"not a zones/regions export (type={data.get('type')!r}); "
            "dreamscape scene JSONs go through "
            "games/wos/events/dreamscape_memory/tools/import_scene_json.py"
        )
    screen = str(data.get("screen", "")).strip()
    raw = data.get("regions")
    if not isinstance(raw, list) or not raw:
        _fail("no regions in the payload")
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, r in enumerate(raw):
        try:
            name = " ".join(str(r["name"]).split())
            bbox = [float(v) for v in r["bbox"]]
        except (KeyError, TypeError, ValueError):
            _fail(f"region #{i}: expected {{name, bbox: [x, y, w, h]}}, got {r!r}")
        if not name:
            _fail(f"region #{i}: empty name — name every zone in the editor first")
        if len(bbox) != 4:
            _fail(f"region {name!r}: bbox must be [x, y, w, h], got {r['bbox']!r}")
        x, y, w, h = bbox
        if not (0 <= x <= 100 and 0 <= y <= 100 and 0 < w <= 100 and 0 < h <= 100):
            _fail(f"region {name!r}: bbox out of the 0–100% range: {bbox}")
        if name in seen:
            _fail(f"duplicate region name {name!r}")
        seen.add(name)
        regions.append({"name": name, "bbox": bbox})
    return screen, regions


def _area_path_for_module(module_dir: Path) -> Path:
    """The module's area file — the ``area:`` override in module.yaml, else the
    first default basename that exists (else area.yaml, to be created)."""
    manifest = module_dir / "module.yaml"
    if manifest.is_file():
        import yaml

        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            _fail(f"cannot parse {manifest}: {exc}")
        override = str(doc.get("area") or "").strip()
        if override:
            return module_dir / override
    for base in _AREA_BASENAMES:
        candidate = module_dir / base
        if candidate.is_file():
            return candidate
    return module_dir / "area.yaml"


def _bbox_dict(bbox: list[float], template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Editor [x, y, w, h] percentages → the area-file bbox dict. When updating,
    carry the existing original_width/height (frames may have been captured at a
    non-default resolution)."""
    x, y, w, h = bbox
    prev = template or {}
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "rotation": prev.get("rotation", 0),
        "original_width": prev.get("original_width", 720),
        "original_height": prev.get("original_height", 1280),
    }


def _find_region(doc: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(screen entry, region dict) for a region name anywhere in the doc."""
    for entry in doc.get("screens") or []:
        for region in entry.get("regions") or []:
            if str(region.get("name", "")).strip() == name:
                return entry, region
    return None


def _screen_entry(doc: dict[str, Any], screen: str, image: str) -> dict[str, Any]:
    screens = doc.setdefault("screens", [])
    for entry in screens:
        if str(entry.get("screen_id", "")).strip() == screen:
            return entry
    max_id = max((int(e.get("id") or 0) for e in screens), default=0)
    entry: dict[str, Any] = {
        "id": max_id + 1,
        "screen_id": screen,
        "ocr": f"references/{image}" if image else "",
        "regions": [],
    }
    screens.append(entry)
    return entry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("json_path", type=Path, help="exported zones JSON from the online editor")
    ap.add_argument(
        "--module",
        required=True,
        type=Path,
        help="module directory whose area file to update, e.g. games/wos/intel",
    )
    ap.add_argument(
        "--screen",
        help="override the payload's screen id (target for NEW regions)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        data = json.loads(args.json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read {args.json_path}: {exc}")
    screen, regions = _validate(data)
    screen = (args.screen or screen).strip()
    if not screen:
        _fail("no screen id — set it in the editor's `screen` field or pass --screen")

    module_dir = args.module if args.module.is_absolute() else _REPO_ROOT / args.module
    if not module_dir.is_dir():
        _fail(f"module directory not found: {module_dir}")
    area_path = _area_path_for_module(module_dir)

    doc = _load_area_file(area_path) if area_path.is_file() else {"version": 2, "screens": []}
    if not isinstance(doc, dict):
        _fail(f"{area_path} did not parse to a mapping")

    updated: list[str] = []
    created: list[str] = []
    for spec in regions:
        name = spec["name"]
        found = _find_region(doc, name)
        if found is not None:
            entry, region = found
            region["bbox"] = _bbox_dict(spec["bbox"], region.get("bbox"))
            where = str(entry.get("screen_id") or "?")
            updated.append(f"{name} (on {where})")
        else:
            entry = _screen_entry(doc, screen, str(data.get("image") or "").strip())
            entry.setdefault("regions", []).append(
                {
                    "name": name,
                    "action": "exist",
                    "threshold": 0.9,
                    "bbox": _bbox_dict(spec["bbox"]),
                }
            )
            created.append(name)

    rel = area_path.relative_to(_REPO_ROOT) if area_path.is_relative_to(_REPO_ROOT) else area_path
    dry = "DRY " if args.dry_run else ""
    for name in updated:
        print(f"{dry}update bbox  {name}")
    for name in created:
        print(f"{dry}create       {name} → screen {screen!r} (action=exist, threshold=0.9 — adjust if OCR/colour)")
    print(f"{dry}write        {rel}  ({len(updated)} updated, {len(created)} created)")
    if args.dry_run:
        return
    # JSON text regardless of suffix — mirrors the dashboard labeling save
    # (api.services.labeling._atomic_write_json), the canonical area format.
    area_path.parent.mkdir(parents=True, exist_ok=True)
    area_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if created:
        print(
            "note: new `exist` regions need reference crops "
            f"({module_dir.name}/references/crop/<screen-stem>_<region>.png) before "
            "template matching can fire — or switch them to ocr/text rules."
        )


if __name__ == "__main__":
    main()
