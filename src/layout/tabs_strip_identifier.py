"""Identify segmented tabs by template-matching their per-page icon.

The segmenter (:mod:`layout.tabs_strip_segmenter`) tells us *where* tabs sit
on the strip and which one is active. To navigate to a specific sub-page
(``shop.daily_deals``, ``shop.get_gems``, …) the bot needs the inverse map:
*which tab leads to which page?* That's what this module does — for each
detected tab it sliding-template-matches a library of per-page icon crops
and reports the best match above a confidence threshold.

The trick that makes this OCR-free: page icons in the strip are the same
overlay illustrations regardless of which tab is currently selected (the
active capsule changes background colour, but the icon on top is identical).
A single ``page.shop.<page>.title`` crop taken from any reference frame
matches the same page's tab on every other strip view.

Returns ``{tab_index: page_name}`` — only confident matches are present, so
callers iterate the dict instead of assuming every tab is identified.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import cv2

from layout.crop_paths import exported_crop_png
from layout.template_match import patch_bgr_from_bbox_percent

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from layout.tabs_strip_segmenter import TabDetection


IDENTIFY_MIN_SCORE = 0.70
"""TM_CCOEFF_NORMED threshold for accepting a template match.

Calibrated on the shop construction_queue reference, where visible tabs
match at 0.83-1.00 and non-visible templates score 0.34-0.48. A floor at
0.70 cleanly separates the two clusters."""


def identify_tabs_by_template(
    image_bgr: np.ndarray,
    tabs: list[TabDetection],
    page_templates: dict[str, np.ndarray],
    *,
    min_score: float = IDENTIFY_MIN_SCORE,
) -> dict[int, str]:
    """For each tab, return the best-matching ``page_name`` template above ``min_score``.

    The match is done inside each tab's bbox (1:1 sliding NCC), so a template
    can only be assigned to the tab whose patch actually contains it. When two
    templates both clear ``min_score`` inside the same tab, the higher one wins.
    Conflicts across tabs (the same template best-matching two different tabs)
    are resolved by keeping the higher-scoring assignment.
    """
    if image_bgr is None or image_bgr.ndim != 3 or not tabs or not page_templates:
        return {}

    # Pre-grayscale every template once.
    tmpl_gray: dict[str, np.ndarray] = {}
    for name, tmpl in page_templates.items():
        if tmpl is None or tmpl.size == 0:
            continue
        gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
        tmpl_gray[name] = gray

    # Score every (tab, page) pair, then assign greedily by highest score so
    # the same template never wins on two tabs at once.
    candidates: list[tuple[float, int, str]] = []
    for tab in tabs:
        patch, _ = patch_bgr_from_bbox_percent(image_bgr, tab.bbox_percent)
        if patch.size == 0:
            continue
        patch_gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        ph, pw = patch_gray.shape
        for name, tg in tmpl_gray.items():
            th, tw = tg.shape
            if th > ph or tw > pw:
                continue
            res = cv2.matchTemplate(patch_gray, tg, cv2.TM_CCOEFF_NORMED)
            _, score, _, _ = cv2.minMaxLoc(res)
            if score < min_score:
                continue
            candidates.append((float(score), tab.index, name))

    candidates.sort(reverse=True)  # highest score first
    out: dict[int, str] = {}
    taken_pages: set[str] = set()
    for _score, idx, name in candidates:
        if idx in out or name in taken_pages:
            continue
        out[idx] = name
        taken_pages.add(name)
    return out


def _canonical_page_id_from_ocr(
    *,
    namespace: str,
    ocr_rel: str,
    screen_id: str,
) -> str | None:
    """Infer ``<namespace>.<page>`` from merged area metadata.

    Deals has several current refs whose ``screen_id`` is still the generic
    ``deals`` node, so the module path is the stronger signal there:
    ``games/wos/deals/hero_rally/references/...`` → ``deals.hero_rally``.
    """
    ns = str(namespace or "").strip()
    if not ns:
        return None
    sid = str(screen_id or "").strip()
    if sid.startswith(f"{ns}."):
        return sid

    parts = ocr_rel.split("/")
    try:
        ns_i = parts.index(ns)
    except ValueError:
        return sid if sid == ns else None
    if ns_i + 1 >= len(parts):
        return sid if sid == ns else None
    module = parts[ns_i + 1].strip()
    if not module:
        return sid if sid == ns else None
    if sid == ns and module == ns:
        return ns
    return f"{ns}.{module}"


def _discover_namespace_active_tab_templates(
    area_doc: dict,
    repo_root: Path,
    strip_bbox: dict,
    *,
    namespace: str,
) -> dict[str, np.ndarray]:
    """Discover tab templates from each reference frame's active tab.

    This is useful for modules such as Deals where tab icons have not been
    annotated as explicit ``<namespace>.to.<page>`` regions yet. The active tab
    is linked to a page via its screen id, then its inner icon area becomes a
    template that can identify the same page on other strip frames.

    Membership is by **screen id / family** (``screen_id == ns`` or
    ``screen_id`` starts with ``"<ns>."``), not by the module's directory: a
    deals-family page contributes its tab template even when the module lives
    under ``games/wos/events/`` rather than ``games/wos/deals/`` (e.g.
    ``vault_of_enigma``, whose screen id is ``deals.vault_of_enigma``).
    """
    import cv2  # local import; keeps module import cheap for non-detection paths

    from layout.tabs_strip_segmenter import detect_tabs_in_strip

    templates: dict[str, np.ndarray] = {}
    if not isinstance(strip_bbox, dict):
        return templates

    ns = str(namespace or "").strip()
    if not ns:
        return templates
    ns_prefix = f"{ns}."

    for screen in area_doc.get("screens", []) or []:
        if not isinstance(screen, dict):
            continue
        screen_id = str(screen.get("screen_id") or "").strip()
        if not (screen_id == ns or screen_id.startswith(ns_prefix)):
            continue
        ocr_rel = str(screen.get("ocr", "")).strip()
        if not ocr_rel:
            continue
        page_id = _canonical_page_id_from_ocr(
            namespace=ns,
            ocr_rel=ocr_rel,
            screen_id=screen_id,
        )
        if not page_id or page_id in templates:
            continue
        image = cv2.imread(str(repo_root / ocr_rel))
        if image is None or image.size <= 0:
            continue
        tabs = detect_tabs_in_strip(image, strip_bbox)
        if not tabs or len(tabs) > 8:
            continue
        active = next((t for t in tabs if t.active), None)
        if active is None:
            continue
        b = active.bbox_percent
        # Crop the top/icon part of the active tab. Keeping the crop away from
        # most of the capsule background makes it transferable to inactive
        # blue tabs on other pages.
        inner_bbox = {
            "x": float(b["x"]) + float(b["width"]) * 0.10,
            "y": float(b["y"]),
            "width": float(b["width"]) * 0.80,
            "height": float(b["height"]) * 0.65,
        }
        patch, _ = patch_bgr_from_bbox_percent(image, inner_bbox)
        if patch.size > 0:
            templates[page_id] = patch
    return templates


def discover_tab_templates(
    area_doc: dict,
    repo_root: Path,
    strip_bbox: dict,
    *,
    namespace: str,
) -> dict[str, np.ndarray]:
    """Auto-discover tab templates for a namespace such as ``shop`` or ``deals``.

    For Shop, this preserves the existing explicit-region conventions. For
    namespaces without per-tab annotations yet, it also derives templates from
    active tabs in reference screenshots.
    """
    import cv2  # local import; this module is otherwise OpenCV-free above

    ns = str(namespace or "").strip()
    if not ns or not isinstance(strip_bbox, dict):
        return {}

    templates: dict[str, np.ndarray] = {}
    if ns == "shop":
        strip_y_lo = float(strip_bbox.get("y", 0.0))
        strip_y_hi = strip_y_lo + float(strip_bbox.get("height", 0.0))

        for screen in area_doc.get("screens", []) or []:
            if not isinstance(screen, dict):
                continue
            # Namespace by screen id / family, not module directory: the shop
            # tab regions are owned by shop-family screens (``shop`` / ``shop.*``)
            # wherever the module physically lives.
            screen_id = str(screen.get("screen_id") or "").strip()
            if not (screen_id == "shop" or screen_id.startswith("shop.")):
                continue
            ocr_rel = str(screen.get("ocr", "")).strip()
            for reg in screen.get("regions", []) or []:
                if not isinstance(reg, dict):
                    continue
                name = str(reg.get("name", "")).strip()
                bbox = reg.get("bbox") or {}
                ry = float(bbox.get("y", 0.0))
                if not (strip_y_lo <= ry < strip_y_hi):
                    continue
                if name.startswith("shop.to."):
                    page_id = "shop." + name[len("shop.to."):]
                elif name.startswith("page.to."):
                    page_id = "shop." + name[len("page.to."):]
                elif name.startswith("page.shop.") and name.endswith(".title"):
                    suffix = name[len("page.shop."):-len(".title")]
                    if not suffix:
                        continue
                    page_id = "shop." + suffix
                else:
                    continue
                if page_id in templates:
                    continue
                crop_path = exported_crop_png(repo_root, ocr_rel, name)
                if not crop_path.is_file():
                    continue
                img = cv2.imread(str(crop_path))
                if img is not None and img.size > 0:
                    templates[page_id] = img

    # Generic fallback, and the primary path for Deals.
    for page_id, img in _discover_namespace_active_tab_templates(
        area_doc,
        repo_root,
        strip_bbox,
        namespace=ns,
    ).items():
        templates.setdefault(page_id, img)
    return templates


def discover_shop_tab_templates(
    area_doc: dict,
    repo_root: Path,
    strip_bbox: dict,
) -> dict[str, np.ndarray]:
    """Auto-discover shop tab templates from area regions inside the strip Y range.

    Three naming conventions are recognised, mirroring how the user annotates:

    * ``shop.to.<page>`` — explicit navigation tap targets on sub-shop pages.
    * ``page.to.<page>`` — tab-icon crops on sub-shop pages (e.g. daily deals
      chest icon); same role as ``shop.to.*`` but uses the ``page.to`` prefix
      from edge-tap routing.
    * ``page.shop.<page>.title`` — the convention used on the shop hub
      (dawn_market) where tab icons share the title suffix. Filtered by
      bbox Y so page-body titles (below the strip) don't slip in.

    Returns ``{page_id → BGR template}``. Page IDs are the canonical
    ``shop.<page>`` form ready to pass to :func:`identify_tabs_by_template`.
    """
    return discover_tab_templates(
        area_doc,
        repo_root,
        strip_bbox,
        namespace="shop",
    )
