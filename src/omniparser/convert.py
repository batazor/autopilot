"""Map OmniParser elements to ``area.json`` region dicts."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from omniparser.types import ParsedUiElement

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify_region_name(content: str, *, fallback: str) -> str:
    base = _SLUG_RE.sub("_", (content or "").lower().strip())[:48].strip("_")
    return base or fallback


def region_name_for_element(el: ParsedUiElement, *, index: int) -> str:
    prefix = "text" if el.type == "text" else "icon"
    slug = slugify_region_name(el.content, fallback=str(index))
    name = f"{prefix}.{slug}"
    if el.type == "icon" and not el.interactivity:
        name = f"{name}.disabled"
    return name


def ratio_xyxy_to_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    image_width: int,
    image_height: int,
) -> dict[str, float | int]:
    ow = max(1, int(image_width))
    oh = max(1, int(image_height))
    x1c = max(0.0, min(1.0, float(x1)))
    y1c = max(0.0, min(1.0, float(y1)))
    x2c = max(0.0, min(1.0, float(x2)))
    y2c = max(0.0, min(1.0, float(y2)))
    if x2c < x1c:
        x1c, x2c = x2c, x1c
    if y2c < y1c:
        y1c, y2c = y2c, y1c
    return {
        "x": 100.0 * x1c,
        "y": 100.0 * y1c,
        "width": 100.0 * (x2c - x1c),
        "height": 100.0 * (y2c - y1c),
        "rotation": 0.0,
        "original_width": ow,
        "original_height": oh,
    }


def _stable_hash_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {str(k): _stable_hash_value(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_stable_hash_value(v) for v in value]
    return value


def region_hash(region: dict[str, object]) -> str:
    """Stable identity hash for an OmniParser region.

    The display ``name`` is intentionally excluded so a later OmniParser pass
    can attach a new name as an alias when the geometry/action identity is the
    same.
    """
    payload = {
        "action": region.get("action"),
        "type": region.get("type"),
        "bbox": region.get("bbox"),
    }
    raw = json.dumps(_stable_hash_value(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def elements_to_regions(
    elements: list[ParsedUiElement],
    *,
    image_width: int,
    image_height: int,
    min_area_pct: float = 0.04,
    existing_names: set[str] | None = None,
) -> list[dict[str, object]]:
    """Convert parsed UI elements to labeling ``regions[]`` entries."""

    taken = {n.strip() for n in (existing_names or set()) if str(n).strip()}
    out: list[dict[str, object]] = []
    for i, el in enumerate(elements):
        x1, y1, x2, y2 = el.bbox
        w_pct = 100.0 * (x2 - x1)
        h_pct = 100.0 * (y2 - y1)
        if w_pct * h_pct < min_area_pct:
            continue
        name = region_name_for_element(el, index=i + 1)
        if name in taken:
            suffix = 2
            candidate = f"{name}.{suffix}"
            while candidate in taken:
                suffix += 1
                candidate = f"{name}.{suffix}"
            name = candidate
        taken.add(name)
        # OmniParser text boxes are labels, not OCR tasks for the bot. Persist
        # every auto-labeled region as a template/hash match to avoid runtime OCR.
        action = "exist"
        rtype = "string"
        region = {
            "name": name,
            "action": action,
            "type": rtype,
            "threshold": 0.9,
            "bbox": ratio_xyxy_to_bbox(
                x1, y1, x2, y2,
                image_width=image_width,
                image_height=image_height,
            ),
        }
        region["hash"] = region_hash(region)
        out.append(region)
    return out
