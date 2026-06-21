"""Shared small helpers for overlay-test (coercion, decode, rule metadata, screen detect)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import cv2
import numpy as np

from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_rules import (
    overlay_rule_screen_allowlist,
    resolved_search_region_for_findicon,
)
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from layout.template_match import _bbox_px_bounds

logger = logging.getLogger(__name__)


def _coerce_float(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return round(float(value), 4)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError):
        return None


def _bbox_pct_to_px(bb: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    """Pixel LTRB; same floor/ceil rounding as labeling crops and ``match_crop_1to1``."""
    bbox = {k: float(bb.get(k) or 0.0) for k in ("x", "y", "width", "height")}
    return _bbox_px_bounds(bbox, hi=h, wi=w)


def _decode_png_to_bgr(png: bytes) -> np.ndarray | None:
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None else None


def _area_region_names(area_doc: dict[str, Any]) -> list[str]:
    """Logical area-region names, including names that only exist in versions."""
    out: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        sources: list[Any] = [screen.get("regions")]
        sources.extend(v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict))
        for source in sources:
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                name = str(reg.get("name") or "").strip()
                if name:
                    out.add(name)
    return sorted(out, key=str.lower)


def _rule_metadata(
    rules_raw: list[dict[str, Any]],
    *,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """For each rule by name: (effective node, search_region resolved, action)."""
    rule_node: dict[str, str] = {}
    rule_search: dict[str, str] = {}
    rule_action: dict[str, str] = {}
    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        if not nm:
            continue
        rule_action[nm] = str(r.get("action") or "").strip()
        gate = overlay_rule_screen_allowlist(r)
        if gate:
            rule_node[nm] = gate[0]
        reg_nm = str(r.get("region") or "").strip()
        pair = (
            screen_region_by_name(area_doc, reg_nm, state_flat=state_flat)
            if reg_nm
            else None
        )
        if pair is not None:
            ref_action = effective_ocr_for_region(pair[0], pair[1])
            sr = resolved_search_region_for_findicon(
                area_doc, reg_nm, ref_action, r, state_flat=state_flat
            )
            if sr:
                rule_search[nm] = sr
    return rule_node, rule_search, rule_action


def _evaluate_rules_ignoring_screen_gate(
    image_bgr: np.ndarray,
    *,
    area_doc: dict[str, Any],
    rules_raw: list[dict[str, Any]],
    repo: Any,
    state_flat: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate every rule regardless of its ``screens`` allowlist.

    Strips ``screens`` from each rule copy so the engine can't short-circuit on
    ``current_screen`` mismatch. Useful for operator probes ("would this rule
    match if its gating were lifted?") without touching the worker's behavior.
    """
    rules_unscoped: list[dict[str, Any]] = []
    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        copy = dict(r)
        copy.pop("screens", None)
        rules_unscoped.append(copy)
    return asyncio.run(
        evaluate_overlay_rules_async(
            image_bgr,
            area_doc,
            repo,
            rules_unscoped,
            current_screen=None,
            state_flat=state_flat,
        )
    )


def _detect_screen_on_frame(
    image_bgr: np.ndarray | None,
    *,
    hint: str | None = None,
) -> tuple[str, int]:
    """Run the same screen detector as the worker on a static PNG frame.

    ``hint``: optional screen id (e.g. the instance's last known ``current_screen``
    from Redis) forwarded to the detector so the sticky verify fast path can
    short-circuit when the hint still holds — turning steady-state cost from a
    full multi-screen scan into one template match.
    """
    started = time.perf_counter()
    detected = ""
    if image_bgr is not None and image_bgr.size > 0:
        try:
            from navigation.detector import suggest_node_for_image_sync

            suggested = suggest_node_for_image_sync(image_bgr, hint=hint)
            if suggested:
                detected = str(suggested).strip()
        except Exception:
            logger.debug("overlay-test: screen detect failed", exc_info=True)
    return detected, int((time.perf_counter() - started) * 1000)


def _ordered_unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = (raw or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out
