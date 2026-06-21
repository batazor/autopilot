"""Detect Mia's Fortune Hut reward-wish item slots.

The reward-wish popup shows four compact cards: an icon in the centre and a
small white quantity in the lower-right corner. Generic OCR is unreliable on
that tiny outlined font, so this module uses two local, deterministic passes:

* icon identity: compare the slot icon crop with curated templates;
* quantity: segment white digit glyphs and match them against digit templates
  learned from the saved reward-wish reference.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np

from config.items import get_item_registry
from config.paths import repo_root as get_repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc

_MODULE_DIR = Path(__file__).resolve().parent
_REWARD_WISH_REFERENCE = _MODULE_DIR / "references" / "page.reward_wish.png"
_REWARD_WISH_SCREEN = "deals.mia_fortune_hut.reward_wish"
_ITEM_REGION_PREFIX = "mia_fortune_hut.reward_wish.item_"
_SLOTS = (1, 2, 3, 4)

# Full card boxes from the popup reference. The public area regions are trimmed
# down to the icon; these wider boxes are only for the quantity parser.
_FULL_SLOT_BBOX_PCT: dict[int, tuple[float, float, float, float]] = {
    1: (16.2, 52.4, 15.2, 8.4),
    2: (33.9, 52.4, 15.2, 8.4),
    3: (51.3, 52.4, 15.2, 8.4),
    4: (69.0, 52.4, 15.2, 8.4),
}
_REFERENCE_AMOUNTS = {1: "150", 2: "18", 3: "120", 4: "40"}


@dataclass(frozen=True, slots=True)
class RewardWishTemplate:
    """One icon template used to identify a reward slot."""

    item_id: str | None
    name: str
    slot: int
    image_bgr: np.ndarray


@dataclass(frozen=True, slots=True)
class RewardWishItem:
    """Detected item and quantity for one reward-wish slot."""

    slot: int
    region: str
    item_id: str | None
    name: str
    amount: int | None
    confidence: float
    amount_confidence: float


def detect_reward_wish_items(
    image_bgr: np.ndarray,
    *,
    repo_root: Path | None = None,
    area_doc: dict[str, Any] | None = None,
) -> list[RewardWishItem]:
    """Detect all four reward-wish slots from a popup frame."""
    return [
        detect_reward_wish_slot(
            image_bgr,
            slot,
            repo_root=repo_root,
            area_doc=area_doc,
        )
        for slot in _SLOTS
    ]


def detect_reward_wish_slot(
    image_bgr: np.ndarray,
    slot: int,
    *,
    repo_root: Path | None = None,
    area_doc: dict[str, Any] | None = None,
) -> RewardWishItem:
    """Detect one reward-wish slot."""
    if slot not in _SLOTS:
        msg = f"slot must be one of {_SLOTS}, got {slot!r}"
        raise ValueError(msg)
    if image_bgr is None or image_bgr.ndim != 3:
        msg = "image_bgr must be a BGR image"
        raise ValueError(msg)

    root = repo_root or get_repo_root()
    doc = area_doc or load_area_doc(root, game="wos")
    region_name = f"{_ITEM_REGION_PREFIX}{slot}"
    pair = screen_region_by_name(doc, region_name, screen_id=_REWARD_WISH_SCREEN)
    if pair is None:
        msg = f"reward-wish item region not found: {region_name}"
        raise KeyError(msg)
    _entry, region = pair
    icon_crop = _crop_bbox_percent(image_bgr, region["bbox"])
    template, confidence = _best_icon_template(icon_crop)

    full_slot = _crop_bbox_percent(image_bgr, _bbox_dict(_FULL_SLOT_BBOX_PCT[slot]))
    amount, amount_conf = parse_reward_wish_amount(full_slot)

    item_id = template.item_id
    name = template.name
    if item_id:
        item_def = get_item_registry().by_id(item_id)
        if item_def is not None:
            name = item_def.name

    return RewardWishItem(
        slot=slot,
        region=region_name,
        item_id=item_id,
        name=name,
        amount=amount,
        confidence=round(confidence, 4),
        amount_confidence=round(amount_conf, 4),
    )


def parse_reward_wish_amount(slot_bgr: np.ndarray) -> tuple[int | None, float]:
    """Read the lower-right quantity from a full reward card crop."""
    glyphs = _extract_quantity_glyphs(slot_bgr)
    if not glyphs:
        return None, 0.0

    templates = _digit_templates()
    digits: list[str] = []
    scores: list[float] = []
    for glyph in glyphs:
        digit, score = _match_digit(glyph, templates)
        if digit is None:
            return None, 0.0
        digits.append(digit)
        scores.append(score)
    if not digits:
        return None, 0.0
    text = "".join(digits)
    try:
        amount = int(text)
    except ValueError:
        return None, 0.0
    return amount, min(scores) if scores else 0.0


def _best_icon_template(icon_bgr: np.ndarray) -> tuple[RewardWishTemplate, float]:
    templates = _reward_wish_templates()
    if not templates:
        return RewardWishTemplate(None, "Unknown", 0, icon_bgr), 0.0
    scored = [(_icon_similarity(icon_bgr, tmpl.image_bgr), tmpl) for tmpl in templates]
    scored.sort(key=lambda row: row[0], reverse=True)
    score, tmpl = scored[0]
    if score < 0.50:
        return RewardWishTemplate(None, "Unknown", 0, icon_bgr), score
    return tmpl, score


@lru_cache(maxsize=1)
def _reward_wish_templates() -> tuple[RewardWishTemplate, ...]:
    """Curated local reward templates.

    ``general_speedup_1h`` is not present in ``db/items`` as a standalone item,
    so it intentionally uses ``item_id=None`` and a display name.
    """
    specs: tuple[tuple[int, str | None, str], ...] = (
        (1, "fire_crystal", "Fire Crystal"),
        (2, "essence_stones", "Essence Stones"),
        (3, None, "1h General Speedup"),
        (4, "gems", "Gems"),
    )
    out: list[RewardWishTemplate] = []
    for slot, item_id, fallback_name in specs:
        crop = _reward_icon_reference_crop(slot)
        if crop is None:
            continue
        name = fallback_name
        if item_id:
            item_def = get_item_registry().by_id(item_id)
            if item_def is not None:
                name = item_def.name
        out.append(RewardWishTemplate(item_id, name, slot, crop))
    return tuple(out)


@lru_cache(maxsize=1)
def _digit_templates() -> dict[str, list[np.ndarray]]:
    ref = cv2.imread(str(_REWARD_WISH_REFERENCE), cv2.IMREAD_COLOR)
    if ref is None:
        return {}

    templates: dict[str, list[np.ndarray]] = {}
    for slot, amount in _REFERENCE_AMOUNTS.items():
        full = _crop_bbox_percent(ref, _bbox_dict(_FULL_SLOT_BBOX_PCT[slot]))
        glyphs = _extract_quantity_glyphs(full)
        if len(glyphs) != len(amount):
            continue
        for digit, glyph in zip(amount, glyphs, strict=True):
            templates.setdefault(digit, []).append(_normalize_digit(glyph))
    return templates


def _match_digit(
    glyph: np.ndarray,
    templates: dict[str, list[np.ndarray]],
) -> tuple[str | None, float]:
    if not templates:
        return None, 0.0
    norm = _normalize_digit(glyph)
    best_digit: str | None = None
    best_score = -1.0
    for digit, variants in templates.items():
        for tmpl in variants:
            same = float(np.mean(norm == tmpl))
            if same > best_score:
                best_score = same
                best_digit = digit
    if best_score < 0.70:
        return None, best_score
    return best_digit, best_score


def _extract_quantity_glyphs(slot_bgr: np.ndarray) -> list[np.ndarray]:
    if slot_bgr is None or slot_bgr.ndim != 3 or slot_bgr.size == 0:
        return []
    h, w = slot_bgr.shape[:2]
    roi = slot_bgr[int(h * 0.66) : int(h * 0.94), int(w * 0.33) : int(w * 0.98)]
    if roi.size == 0:
        return []
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = (gray > 170).astype(np.uint8) * 255
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)

    components: list[tuple[int, np.ndarray]] = []
    for idx in range(1, count):
        x, y, ww, hh, area = (int(v) for v in stats[idx])
        if y < 8 or ww < 2 or hh < 8 or area < 8 or area > 160:
            continue
        glyph = (labels[y : y + hh, x : x + ww] == idx).astype(np.uint8) * 255
        components.append((x, glyph))
    components.sort(key=lambda row: row[0])
    return [glyph for _x, glyph in components]


def _normalize_digit(glyph: np.ndarray) -> np.ndarray:
    padded = cv2.copyMakeBorder(glyph, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=0)
    resized = cv2.resize(padded, (18, 26), interpolation=cv2.INTER_NEAREST)
    return (resized > 0).astype(np.uint8)


def _reward_icon_reference_crop(slot: int) -> np.ndarray | None:
    ref = cv2.imread(str(_REWARD_WISH_REFERENCE), cv2.IMREAD_COLOR)
    if ref is None:
        return None
    # Keep this tied to area.yaml's trimmed icon regions.
    bbox_by_slot = {
        1: (17.7, 53.2, 9.0, 5.2),
        2: (35.3, 53.2, 9.0, 5.2),
        3: (52.8, 53.2, 9.0, 5.2),
        4: (70.4, 53.2, 9.0, 5.2),
    }
    return _crop_bbox_percent(ref, _bbox_dict(bbox_by_slot[slot]))


def _icon_similarity(a_bgr: np.ndarray, b_bgr: np.ndarray) -> float:
    a = _normalize_icon(a_bgr)
    b = _normalize_icon(b_bgr)
    color_mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    color_score = max(0.0, 1.0 - color_mse / (255.0 * 255.0))

    ae = cv2.Canny(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), 60, 160)
    be = cv2.Canny(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), 60, 160)
    edge_score = float(np.mean((ae > 0) == (be > 0)))
    return 0.75 * color_score + 0.25 * edge_score


def _normalize_icon(image_bgr: np.ndarray) -> np.ndarray:
    return cv2.resize(image_bgr, (64, 64), interpolation=cv2.INTER_AREA)


def _crop_bbox_percent(image_bgr: np.ndarray, bbox: dict[str, float]) -> np.ndarray:
    h, w = image_bgr.shape[:2]
    x0 = max(0, min(w, int(round(float(bbox["x"]) / 100.0 * w))))
    y0 = max(0, min(h, int(round(float(bbox["y"]) / 100.0 * h))))
    x1 = max(x0, min(w, int(round((float(bbox["x"]) + float(bbox["width"])) / 100.0 * w))))
    y1 = max(y0, min(h, int(round((float(bbox["y"]) + float(bbox["height"])) / 100.0 * h))))
    return image_bgr[y0:y1, x0:x1]


def _bbox_dict(values: tuple[float, float, float, float]) -> dict[str, float]:
    x, y, width, height = values
    return {"x": x, "y": y, "width": width, "height": height}
