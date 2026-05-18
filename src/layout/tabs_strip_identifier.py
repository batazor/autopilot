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

from layout.template_match import patch_bgr_from_bbox_percent

if TYPE_CHECKING:
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
