"""OmniParser → geometry / NMS → ``area.json`` region dicts.

Uses the same NMS algorithm as Roboflow ``supervision`` (see :mod:`omniparser._nms`), without
importing ``supervision`` itself (avoids OpenCV conflicts when multiple ``cv2`` wheels are
installed).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from omniparser._nms import non_max_suppression
from omniparser.convert import (
    ratio_xyxy_to_bbox,
    region_hash,
    region_name_for_element,
)
from omniparser.types import ParsedUiElement

OMNIPARSER_CROP_HASH_BLACKLIST: frozenset[str] = frozenset(
    {
        "3bd39f05ac16b1ba678908b8240853d1f3346051ad89782fb1fc10a817f162c2",
        "25d8c3d35b3e3f7985656beecadd4ebbebbe58df7706ea1890ab66a3c53de57d",
        "f662a6201f1fa74f8236d2f55999856bcf6c95d3d5f120b6ef03819a07fc9dc7",
        "f5d4abad731b372f880b0a0e2b5a6688a9e6729340e6e39ed8d172c1b55fee0c",
        "de837c3359ca5e069319ddac57e5657b3020ddc875adeb5f8f8af68baca0fc7a",
    }
)
OMNIPARSER_NAME_BLACKLIST_PREFIXES: tuple[str, ...] = (
    "icon.unanswerable",
    "text.6_6",
)
OMNIPARSER_MATCHED_EXISTING_KEY = "_omni_matched_existing"


@dataclass(frozen=True)
class OmniParserProposalStats:
    raw_element_count: int
    skipped_min_area: int
    after_min_area_count: int
    after_nms_count: int
    nms_removed: int
    blacklist_skipped: int


_ICON_CONF_RE = re.compile(r"(?i)icon\s+([0-9]*\.?[0-9]+)")


@dataclass(frozen=True)
class OmniDetections:
    """Lightweight stand-in for ``sv.Detections`` (pixel ``xyxy``, parallel ``payloads``)."""

    xyxy: np.ndarray
    confidence: np.ndarray
    class_id: np.ndarray
    payloads: list[ParsedUiElement]

    def __len__(self) -> int:
        return int(self.xyxy.shape[0])


def _confidence_for_element(el: ParsedUiElement) -> float:
    if el.type == "text":
        return 1.0
    m = _ICON_CONF_RE.match((el.content or "").strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.99
    return 0.99


def _class_id_for(el: ParsedUiElement) -> int:
    return 0 if el.type == "icon" else 1


def _element_area_metric(el: ParsedUiElement) -> float:
    """Same product rule as ``elements_to_regions`` in ``convert.py``."""

    x1, y1, x2, y2 = el.bbox
    w_pct = 100.0 * (x2 - x1)
    h_pct = 100.0 * (y2 - y1)
    return w_pct * h_pct


def filter_elements_min_area(elements: list[ParsedUiElement], *, min_area_pct: float) -> list[ParsedUiElement]:
    return [el for el in elements if _element_area_metric(el) >= float(min_area_pct)]


def elements_to_detections(elements: list[ParsedUiElement], *, image_width: int, image_height: int) -> OmniDetections:
    ow = max(1, int(image_width))
    oh = max(1, int(image_height))
    if not elements:
        return OmniDetections(
            xyxy=np.zeros((0, 4), dtype=np.float32),
            confidence=np.zeros((0,), dtype=np.float32),
            class_id=np.zeros((0,), dtype=np.int32),
            payloads=[],
        )
    xyxy_rows: list[list[float]] = []
    confidences: list[float] = []
    class_ids: list[int] = []
    payloads: list[ParsedUiElement] = []
    for el in elements:
        x1, y1, x2, y2 = el.bbox
        xyxy_rows.append([x1 * ow, y1 * oh, x2 * ow, y2 * oh])
        confidences.append(_confidence_for_element(el))
        class_ids.append(_class_id_for(el))
        payloads.append(el)
    return OmniDetections(
        xyxy=np.asarray(xyxy_rows, dtype=np.float32),
        confidence=np.asarray(confidences, dtype=np.float32),
        class_id=np.asarray(class_ids, dtype=np.int32),
        payloads=payloads,
    )


def detections_to_regions(
    detections: OmniDetections,
    *,
    image_width: int,
    image_height: int,
    existing_names: set[str] | None = None,
) -> list[dict[str, object]]:
    if len(detections) == 0:
        return []
    taken = {str(n).strip() for n in (existing_names or set()) if str(n).strip()}
    ow = max(1, int(image_width))
    oh = max(1, int(image_height))

    out: list[dict[str, object]] = []
    for i in range(len(detections)):
        px1, py1, px2, py2 = detections.xyxy[i].tolist()
        x1_r = px1 / ow
        y1_r = py1 / oh
        x2_r = px2 / ow
        y2_r = py2 / oh
        payload = detections.payloads[i]

        idx = len(out)

        name = region_name_for_element(payload, index=idx + 1)
        if name in taken:
            suffix = 2
            candidate_nm = f"{name}.{suffix}"
            while candidate_nm in taken:
                suffix += 1
                candidate_nm = f"{name}.{suffix}"
            name = candidate_nm
        taken.add(str(name))

        # OmniParser may call the element text, but the generated area region
        # should still be a cheap template/hash check at runtime.
        action = "exist"
        rtype = "string"

        region = {
            "name": name,
            "action": action,
            "type": rtype,
            "threshold": 0.9,
            "bbox": ratio_xyxy_to_bbox(x1_r, y1_r, x2_r, y2_r, image_width=ow, image_height=oh),
        }
        region["hash"] = region_hash(region)
        out.append(region)
    return out


def filter_detections_nms(detections: OmniDetections, *, iou_threshold: float) -> OmniDetections:
    if len(detections) == 0:
        return detections
    thr = float(iou_threshold)
    preds = np.hstack([detections.xyxy, detections.confidence.reshape(-1, 1)])
    keep = non_max_suppression(preds, iou_threshold=thr)
    idx = np.flatnonzero(keep)
    return OmniDetections(
        xyxy=np.asarray(detections.xyxy[idx], dtype=np.float32),
        confidence=np.asarray(detections.confidence[idx], dtype=np.float32),
        class_id=np.asarray(detections.class_id[idx], dtype=np.int32),
        payloads=[detections.payloads[int(j)] for j in idx],
    )


filter_detections = filter_detections_nms


def filter_blacklisted_regions(
    image: Image.Image,
    regions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    kept: list[dict[str, object]] = []
    skipped = 0
    for region in regions:
        if is_blacklisted_omniparser_region(image, region):
            skipped += 1
            continue
        kept.append(region)
    return kept, skipped


def _bbox_rect(region: dict[str, object]) -> tuple[float, float, float, float] | None:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w = float(bbox.get("width", 0.0))
        h = float(bbox.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h)


def _rects_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection_area(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h


def _overlap_ratio_of_smaller(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    smaller = min(_rect_area(a), _rect_area(b))
    if smaller <= 0.0:
        return 0.0
    return _intersection_area(a, b) / smaller


def _has_bbox_intersection(region: dict[str, object], existing_rects: list[tuple[float, float, float, float]]) -> bool:
    rect = _bbox_rect(region)
    if rect is None:
        return False
    return any(_rects_intersect(rect, existing) for existing in existing_rects)


def _region_names(region: dict[str, object]) -> list[str]:
    out: list[str] = []
    name = str(region.get("name") or "").strip()
    if name:
        out.append(name)
    aliases = region.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            alias_s = str(alias or "").strip()
            if alias_s and alias_s not in out:
                out.append(alias_s)
    return out


def _is_matching_auxiliary_region(region: dict[str, object]) -> bool:
    if bool(region.get("overlay_auxiliary")):
        return True
    return any(name.endswith(("_search", "_tap")) for name in _region_names(region))


def _region_identity_hashes(region: dict[str, object]) -> set[str]:
    h = str(region.get("hash") or "").strip()
    hashes = {region_hash(region)}
    if h:
        hashes.add(h)
    return hashes


def _copy_bbox_from_existing(
    proposal: dict[str, object],
    existing_bbox: dict[str, object],
) -> bool:
    before = proposal.get("bbox")
    proposal["bbox"] = dict(existing_bbox)
    proposal["hash"] = region_hash(proposal)
    return before != proposal["bbox"]


def _mark_matched_existing(proposal: dict[str, object]) -> None:
    proposal[OMNIPARSER_MATCHED_EXISTING_KEY] = True


def _strip_internal_proposal_fields(region: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in region.items()
        if not str(key).startswith("_omni_")
    }


def _region_crop_pixel_hash(image: Image.Image, region: dict[str, object]) -> str | None:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w_pct = float(bbox.get("width", 0.0))
        h_pct = float(bbox.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if w_pct <= 0 or h_pct <= 0:
        return None

    image_rgba = image.convert("RGBA")
    ow, oh = image_rgba.size
    left = x / 100.0 * ow
    top = y / 100.0 * oh
    width = w_pct / 100.0 * ow
    height = h_pct / 100.0 * oh
    l_px = max(0, min(math.floor(left), ow - 1))
    t_px = max(0, min(math.floor(top), oh - 1))
    r_px = max(l_px + 1, min(math.ceil(left + width), ow))
    b_px = max(t_px + 1, min(math.ceil(top + height), oh))
    crop = image_rgba.crop((l_px, t_px, r_px, b_px))
    return hashlib.sha256(crop.tobytes()).hexdigest()


def is_blacklisted_omniparser_region(image: Image.Image, region: dict[str, object]) -> bool:
    name = str(region.get("name") or "").strip().lower()
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in OMNIPARSER_NAME_BLACKLIST_PREFIXES):
        return True
    crop_hash = _region_crop_pixel_hash(image, region)
    return bool(crop_hash and crop_hash in OMNIPARSER_CROP_HASH_BLACKLIST)


def reuse_proposal_names_from_existing_crops(
    image: Image.Image,
    proposals: list[dict[str, object]],
    existing: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """If a proposal overlaps an existing region and crop pixels match, use that region name.

    Compared hashes are from :func:`_region_crop_pixel_hash` (RGBA crop SHA-256), not
    :func:`omniparser.convert.region_hash` (geometry-only). Helps when OmniParser shifts
    the box slightly but the tile content matches an already-labeled region.
    """

    reused = 0
    existing_entries: list[tuple[tuple[float, float, float, float], str, str, dict[str, object]]] = []
    for er in existing:
        if _is_matching_auxiliary_region(er):
            continue
        rect = _bbox_rect(er)
        bbox = er.get("bbox")
        if rect is None or not isinstance(bbox, dict):
            continue
        ch = _region_crop_pixel_hash(image, er)
        if not ch:
            continue
        names = _region_names(er)
        primary = names[0] if names else ""
        if not primary:
            continue
        existing_entries.append((rect, primary, ch, bbox))

    for reg in proposals:
        prop_rect = _bbox_rect(reg)
        if prop_rect is None:
            continue
        prop_hash = _region_crop_pixel_hash(image, reg)
        if not prop_hash:
            continue
        cur_name = str(reg.get("name") or "").strip()
        for ex_rect, primary, ex_hash, ex_bbox in existing_entries:
            if not _rects_intersect(prop_rect, ex_rect):
                continue
            if prop_hash != ex_hash:
                continue
            _mark_matched_existing(reg)
            changed = _copy_bbox_from_existing(reg, ex_bbox)
            if cur_name != primary:
                reg["name"] = primary
                reg["hash"] = region_hash(reg)
                reused += 1
            elif changed:
                reused += 1
            break

    return proposals, reused


def reuse_proposal_names_from_overlapping_regions(
    proposals: list[dict[str, object]],
    existing: list[dict[str, object]],
    *,
    threshold: float = 0.8,
) -> tuple[list[dict[str, object]], int, int]:
    """Rename proposal regions to existing names when bbox overlap proves identity.

    If multiple proposal boxes map to the same existing region, keep the strongest
    overlap and drop the rest so saving cannot create duplicate region names.
    """

    score_tie_epsilon = 0.05
    existing_entries: list[tuple[tuple[float, float, float, float], str, float, dict[str, object]]] = []
    for er in existing:
        if _is_matching_auxiliary_region(er):
            continue
        rect = _bbox_rect(er)
        bbox = er.get("bbox")
        if rect is None or not isinstance(bbox, dict):
            continue
        names = _region_names(er)
        primary = names[0] if names else ""
        if primary:
            existing_entries.append((rect, primary, _rect_area(rect), bbox))

    best_by_name: dict[str, tuple[float, int]] = {}
    match_by_idx: dict[int, tuple[str, float, dict[str, object]]] = {}
    threshold = float(threshold)
    for idx, reg in enumerate(proposals):
        prop_rect = _bbox_rect(reg)
        if prop_rect is None:
            continue
        best_name = ""
        best_score = 0.0
        best_area = float("inf")
        best_bbox: dict[str, object] | None = None
        for ex_rect, primary, existing_area, existing_bbox in existing_entries:
            score = _overlap_ratio_of_smaller(prop_rect, ex_rect)
            if score < threshold:
                continue
            if (
                score > best_score + score_tie_epsilon
                or (abs(score - best_score) <= score_tie_epsilon and existing_area < best_area)
            ):
                best_name = primary
                best_score = score
                best_area = existing_area
                best_bbox = existing_bbox
        if best_name and best_bbox is not None:
            match_by_idx[idx] = (best_name, best_score, best_bbox)
            prev = best_by_name.get(best_name)
            if prev is None or best_score > prev[0]:
                best_by_name[best_name] = (best_score, idx)

    keep_indices = {
        winner_idx
        for _score, winner_idx in best_by_name.values()
    }
    out: list[dict[str, object]] = []
    reused = 0
    dropped = 0
    for idx, reg in enumerate(proposals):
        match = match_by_idx.get(idx)
        if match is None:
            out.append(reg)
            continue
        canonical_name, _score, canonical_bbox = match
        if idx not in keep_indices:
            dropped += 1
            continue
        _mark_matched_existing(reg)
        changed = _copy_bbox_from_existing(reg, canonical_bbox)
        if str(reg.get("name") or "").strip() != canonical_name:
            reg["name"] = canonical_name
            reg["hash"] = region_hash(reg)
            reused += 1
        elif changed:
            reused += 1
        out.append(reg)
    return out, reused, dropped


def merge_omniparser_regions(
    existing: list[dict[str, object]],
    proposed: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int, int, int]:
    """Merge proposed OmniParser regions into the current screen only.

    Returns ``(merged, added, aliased, skipped_intersections)``. If a proposed
    region has the same identity hash as an existing current-screen region, the
    proposed name becomes an alias instead of creating a duplicate bbox.
    """
    merged = list(existing)
    names = {name for region in merged for name in _region_names(region)}
    by_hash: dict[str, dict[str, object]] = {}
    for region in merged:
        if _is_matching_auxiliary_region(region):
            continue
        for h in _region_identity_hashes(region):
            by_hash.setdefault(h, region)

    existing_rects: list[tuple[dict[str, object], tuple[float, float, float, float]]] = [
        (region, rect)
        for region in merged
        if not _is_matching_auxiliary_region(region) and (rect := _bbox_rect(region)) is not None
    ]
    added = 0
    aliased = 0
    skipped_intersections = 0

    def _add_region_alias(region: dict[str, object], alias: str, taken_names: set[str]) -> bool:
        alias_s = alias.strip()
        if not alias_s or alias_s in _region_names(region) or alias_s in taken_names:
            return False
        aliases = region.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
            region["aliases"] = aliases
        aliases.append(alias_s)
        taken_names.add(alias_s)
        return True

    for reg in proposed:
        nm = str(reg.get("name") or "").strip()
        matched_region = next((by_hash[h] for h in _region_identity_hashes(reg) if h in by_hash), None)
        rect = _bbox_rect(reg)
        if matched_region is None and rect is not None:
            matched_region = next(
                (
                    existing_region
                    for existing_region, existing_rect in existing_rects
                    if _overlap_ratio_of_smaller(rect, existing_rect) >= 0.8
                ),
                None,
            )
        if matched_region is not None:
            if _add_region_alias(matched_region, nm, names):
                aliased += 1
            continue
        if nm in names:
            continue
        if _has_bbox_intersection(reg, [existing_rect for _region, existing_rect in existing_rects]):
            skipped_intersections += 1
            continue
        clean_reg = _strip_internal_proposal_fields(reg)
        merged.append(clean_reg)
        for name in _region_names(clean_reg):
            names.add(name)
        for h in _region_identity_hashes(clean_reg):
            by_hash.setdefault(h, clean_reg)
        if rect is not None:
            existing_rects.append((clean_reg, rect))
        added += 1
    return merged, added, aliased, skipped_intersections


def merge_detected_regions(
    *,
    merge_mode: str,
    existing: list[dict[str, object]],
    proposed_regions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int, int, int]:
    """Apply merge or replace semantics to prepared proposal regions."""

    if merge_mode == "replace":
        cleaned = [_strip_internal_proposal_fields(region) for region in proposed_regions]
        return cleaned, len(cleaned), 0, 0
    return merge_omniparser_regions(existing, proposed_regions)


merge_detections_into_regions = merge_detected_regions


def build_omniparser_proposal_regions(
    elements: tuple[ParsedUiElement, ...] | list[ParsedUiElement],
    image: Image.Image,
    *,
    width: int,
    height: int,
    min_area_pct: float,
    nms_iou_threshold: float,
) -> tuple[list[dict[str, object]], OmniParserProposalStats]:
    """OmniParser → min-area → NMS (supervision-compatible) → ``area.json`` style regions."""

    raw_list = list(elements)
    stats_raw = len(raw_list)
    after_min_el = filter_elements_min_area(raw_list, min_area_pct=float(min_area_pct))
    skipped_min = stats_raw - len(after_min_el)
    after_min_cnt = len(after_min_el)

    detections_in = elements_to_detections(after_min_el, image_width=width, image_height=height)
    cnt_before_nms = len(detections_in)
    deduped = filter_detections_nms(detections_in, iou_threshold=float(nms_iou_threshold))
    after_nms = len(deduped)
    nms_removed = max(0, cnt_before_nms - after_nms)

    regions_unclean = detections_to_regions(deduped, image_width=width, image_height=height)
    filtered, blacklist_skip = filter_blacklisted_regions(image, regions_unclean)

    stats = OmniParserProposalStats(
        raw_element_count=stats_raw,
        skipped_min_area=int(skipped_min),
        after_min_area_count=int(after_min_cnt),
        after_nms_count=int(after_nms),
        nms_removed=int(nms_removed),
        blacklist_skipped=int(blacklist_skip),
    )
    return filtered, stats


def parsed_element_to_dict(el: ParsedUiElement) -> dict[str, object]:
    return {
        "type": el.type,
        "bbox": list(el.bbox),
        "interactivity": el.interactivity,
        "content": el.content,
    }


def parsed_element_from_dict(obj: dict[str, object]) -> ParsedUiElement:
    bbox_raw = obj.get("bbox")
    if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
        msg = "invalid bbox"
        raise ValueError(msg)
    bbox = tuple(float(bbox_raw[i]) for i in range(4))  # type: ignore[misc]
    el_type_raw = str(obj.get("type") or "icon").strip().lower()
    el_type: object = el_type_raw if el_type_raw in ("icon", "text") else "icon"
    return ParsedUiElement(
        type=el_type,  # type: ignore[arg-type]
        bbox=bbox,  # type: ignore[arg-type]
        interactivity=bool(obj.get("interactivity", False)),
        content=str(obj.get("content") or "").strip(),
    )


def deserialize_parsed_elements(raw: list[dict[str, object]]) -> list[ParsedUiElement]:
    return [parsed_element_from_dict(d) for d in raw]

