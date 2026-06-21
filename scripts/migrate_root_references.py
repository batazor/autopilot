"""Migrate root references/*.png entries into per-module references/ + area.yaml.

Run once via ``uv run python scripts/migrate_root_references.py``.
Removes processed entries from root ``area.json`` and moves PNGs (+ matching
``references/crop/*`` files) into the target module directory.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
ROOT_AREA = REPO / "area.json"
ROOT_REFS = REPO / "references"

# Each value: target module directory (relative to repo) or None to drop the
# entry entirely from area.json (orphan). "mode" is "move" (move file + entry)
# or "cleanup" (file already in module, just drop entry from root area.json).
MAPPING: dict[str, tuple[str | None, str]] = {
    # building / common
    "references/add_worker_button.png": ("modules/core/building/common", "move"),
    "references/build_building_item.png": ("modules/core/building/common", "move"),
    "references/build_button.png": ("modules/core/building/common", "move"),
    "references/building.upgrade.png": ("modules/core/building/common", "move"),
    "references/building.upgrading.png": ("modules/core/building/common", "move"),
    "references/go_big_button.png": ("modules/core/building/common", "move"),
    "references/go_button.png": ("modules/core/building/common", "move"),
    "references/page.building.furniture.png": ("modules/core/building/common", "move"),
    "references/upgrade_big_button.png": ("modules/core/building/common", "move"),
    "references/upgrade_building.png": ("modules/core/building/common", "move"),
    "references/upgrade_button.png": ("modules/core/building/common", "move"),
    # building / furnace
    "references/building.furnace.png": ("modules/core/building/furnace", "move"),
    # core/common
    "references/big_claim.png": ("modules/core/common", "move"),
    "references/big_claim_button.png": ("modules/core/common", "move"),
    "references/button.done.png": ("modules/core/common", "move"),
    "references/button.next.png": ("modules/core/common", "move"),
    "references/claim.png": ("modules/core/common", "cleanup"),
    "references/claim_all.png": ("modules/core/common", "move"),
    "references/dontshowthisagaintoday.png": ("modules/core/common", "move"),
    "references/hand_pointer.png": ("modules/core/common", "move"),
    "references/hand_pointer_small.png": ("modules/core/common", "move"),
    "references/hand_pointer_small_reverse.png": ("modules/core/common", "move"),
    "references/page.loading.png": ("modules/core/common", "move"),
    "references/retry_page.png": ("modules/core/common", "move"),
    "references/survivors_have_arrived.png": ("modules/core/common", "move"),
    "references/tapanywhereyoexit.png": ("modules/core/common", "move"),
    "references/ui.button.confirm_blue.png": ("modules/core/common", "move"),
    # alliance / join_to_alliance (NEW module)
    "references/alliance.invitation.png": (
        "modules/alliance/join_to_alliance",
        "move",
    ),
    # heroes (feature module)
    "references/box.gift.png": ("modules/heroes", "move"),
    "references/page.hero.recrutment.png": ("modules/heroes", "move"),
    "references/page.heroes.3.png": ("modules/heroes", "move"),
    "references/page.heroes.png": ("modules/heroes", "move"),
    "references/page.heroes.unit.png": ("modules/heroes", "move"),
    # mail
    "references/mail_page.png": ("modules/mail", "move"),
    "references/postman.png": ("modules/mail", "move"),
    # core/exploration
    "references/exploration.png": ("modules/core/exploration", "move"),
    "references/page.squad_settings.png": ("modules/core/exploration", "cleanup"),
    "references/page.squad_settings.status.defeat.png": (
        "modules/core/exploration",
        "cleanup",
    ),
    # core/main_city — already migrated, just drop root entry
    "references/main_city_v2.png": ("modules/core/main_city", "cleanup"),
    # core/shop
    "references/page.shop.png": ("modules/core/shop", "move"),
    # core/popup (frostdragon_tyrant)
    "references/page.tyrant.png": ("modules/core/popup", "move"),
}


def load_yaml_or_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "screens": []}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {"version": 2, "screens": []}
    if path.suffix == ".json" or text.lstrip().startswith("{"):
        return json.loads(text)
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {"version": 2, "screens": []}


def dump_yaml_or_json(path: Path, doc: dict[str, Any], style: str) -> None:
    if style == "json":
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


def detect_style(path: Path) -> str:
    if not path.is_file():
        return "json"  # default for new files
    text = path.read_text(encoding="utf-8")
    return "json" if text.lstrip().startswith("{") else "yaml"


def rebase_ocr_to_module(ocr: str, module_dir: Path) -> str:
    """Convert ``references/foo.png`` → ``references/foo.png`` (module-relative).

    The area-manifest loader rebases bare ``references/`` paths against the
    module root automatically, so we keep the string as-is for module yamls.
    """
    raw = ocr.strip()
    if raw.startswith("modules/"):
        return raw
    return raw  # keep "references/<name>.png" — loader rebases to module root


def move_file(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def move_crops_for_png(png_name: str, src_crop: Path, dst_crop: Path) -> list[str]:
    """Move ``references/crop/<png_stem>_*.png`` to module's references/crop/."""
    stem = Path(png_name).stem
    moved: list[str] = []
    if not src_crop.is_dir():
        return moved
    for crop in src_crop.glob(f"{stem}_*.png"):
        dst = dst_crop / crop.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(crop), str(dst))
        moved.append(crop.name)
    return moved


def main() -> None:
    area_doc = json.loads(ROOT_AREA.read_text(encoding="utf-8"))
    screens: list[dict[str, Any]] = list(area_doc.get("screens") or [])

    # Group entries to migrate by target module
    by_module: dict[str, list[dict[str, Any]]] = {}
    cleanup_keys: set[str] = set()
    move_keys: set[str] = set()

    for screen in screens:
        ocr = str(screen.get("ocr") or "").replace("\\", "/")
        if ocr not in MAPPING:
            continue
        target, mode = MAPPING[ocr]
        if target is None:
            cleanup_keys.add(ocr)
            continue
        by_module.setdefault(target, []).append(screen)
        if mode == "move":
            move_keys.add(ocr)
        else:
            cleanup_keys.add(ocr)

    # Process file moves first
    for ocr_path, (target, mode) in MAPPING.items():
        if mode != "move" or target is None:
            continue
        src_png = REPO / ocr_path
        dst_png = REPO / target / ocr_path  # target/references/<name>.png
        moved = move_file(src_png, dst_png)
        if moved:
            print(f"moved {src_png.relative_to(REPO)} -> {dst_png.relative_to(REPO)}")
        # crops
        crop_moved = move_crops_for_png(
            Path(ocr_path).name,
            REPO / "references" / "crop",
            REPO / target / "references" / "crop",
        )
        for c in crop_moved:
            print(f"  + crop {c}")

    # Append entries to target area.yaml/json
    for target, entries in by_module.items():
        area_path = REPO / target / "area.yaml"
        if not area_path.is_file():
            # also accept area.json
            alt = REPO / target / "area.json"
            if alt.is_file():
                area_path = alt
        style = detect_style(area_path)
        doc = load_yaml_or_json(area_path)
        doc.setdefault("version", 2)
        existing = list(doc.get("screens") or [])
        existing_ids = {
            (s.get("id"), s.get("ocr")) for s in existing if isinstance(s, dict)
        }
        added = 0
        for entry in entries:
            key = (entry.get("id"), entry.get("ocr"))
            if key in existing_ids:
                continue
            existing.append(entry)
            existing_ids.add(key)
            added += 1
        doc["screens"] = existing
        area_path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml_or_json(area_path, doc, style)
        print(f"appended {added} screens -> {area_path.relative_to(REPO)}")

    # Trim root area.json
    drop = move_keys | cleanup_keys
    remaining = [
        s for s in screens if str(s.get("ocr") or "").replace("\\", "/") not in drop
    ]
    area_doc["screens"] = remaining
    ROOT_AREA.write_text(
        json.dumps(area_doc, indent=2) + "\n", encoding="utf-8"
    )
    print(f"trimmed area.json: {len(screens)} -> {len(remaining)} screens")


if __name__ == "__main__":
    main()
