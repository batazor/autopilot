"""Image-based screen-verification rules, extracted from ``Navigator``.

A ``screen_verify.yaml`` rule confirms the bot really landed on a target screen
after a navigation hop. Each rule is one of:

- ``match`` — template/findIcon match of a labelled region (overlay engine).
- ``ocr`` — OCR a region and check confidence / ``contains`` substrings (with a
  fuzzy fallback).
- ``tab_active`` — the named tab region reads as the active tab.
- ``from_screen`` — the immediately-previous screen in history is in the allowed
  set (no image needed; reads :class:`navigation.nav_state.NavStateStore`).

``ScreenVerifier`` owns the per-rule arithmetic so ``Navigator`` can stay focused
on routing; Navigator keeps thin ``_verify_*`` forwarders (some are patched /
called directly by tests) that delegate here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.types import Region
from ocr.fuzzy import match as fuzzy_match

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import numpy as np

    from navigation.nav_state import NavStateStore
    from ocr.client import OcrClient

logger = logging.getLogger(__name__)


class ScreenVerifier:
    def __init__(
        self,
        *,
        load_area_doc: Callable[[], dict[str, Any]],
        get_ocr: Callable[[], OcrClient],
        repo_root: Path,
        screen_state: NavStateStore,
    ) -> None:
        # ``load_area_doc`` / ``get_ocr`` are getters (not captured values) so a
        # test that reassigns ``nav._area_doc`` / ``nav._ocr`` after construction
        # is still observed here. ``repo_root`` / ``screen_state`` never change
        # post-construction, so they're held directly.
        self._load_area_doc = load_area_doc
        self._get_ocr = get_ocr
        self._repo_root = repo_root
        self._screen_state = screen_state

    async def verify_match_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        region = str(rule.get("match") or "").strip()
        if not region:
            return False
        threshold_raw = rule.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.9
        except (TypeError, ValueError):
            threshold = 0.9
        action = str(rule.get("action") or "findIcon").strip() or "findIcon"
        overlay_rule: dict[str, Any] = {
            "name": f"navigator.verify.{region}",
            "action": action,
            "region": region,
            "threshold": threshold,
        }
        min_sat = rule.get("min_match_saturation")
        if min_sat is not None:
            overlay_rule["min_match_saturation"] = min_sat
        for key in (
            "type",
            "min_mask_share",
            "min_component_width_ratio",
            "min_component_y_ratio",
            "min_component_height_ratio",
            "min_component_area_ratio",
            "template",
            "search_region",
        ):
            val = rule.get(key)
            if val is not None:
                overlay_rule[key] = val
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                self._repo_root,
                [overlay_rule],
                state_flat=state_flat,
            )
        except Exception:
            logger.debug("Navigator: match verify failed for %s", region, exc_info=True)
            return False
        row = out.get(str(overlay_rule["name"]))
        return bool(isinstance(row, dict) and row.get("matched"))

    async def verify_ocr_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        region = str(rule.get("ocr") or "").strip()
        if not region:
            return False
        pair = screen_region_by_name(self._load_area_doc(), region, state_flat=state_flat)
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning("Navigator: OCR verify region %r not found", region)
            return False
        bbox = pair[1]["bbox"]
        h, w = int(image.shape[0]), int(image.shape[1])
        try:
            px = int(round(float(bbox["x"]) / 100.0 * w))
            py = int(round(float(bbox["y"]) / 100.0 * h))
            pw = int(round(float(bbox["width"]) / 100.0 * w))
            ph = int(round(float(bbox["height"]) / 100.0 * h))
        except (KeyError, TypeError, ValueError):
            return False
        if pw <= 0 or ph <= 0:
            return False
        try:
            result = await self._get_ocr().ocr_region(
                image, Region(px, py, pw, ph), region_id=region
            )
        except Exception:
            logger.debug("Navigator: OCR verify failed for %s", region, exc_info=True)
            return False

        conf_raw = rule.get("confidence")
        try:
            min_conf = float(conf_raw) if conf_raw is not None else 0.0
        except (TypeError, ValueError):
            min_conf = 0.0
        if float(result.confidence or 0.0) < min_conf:
            return False

        contains_raw = rule.get("contains")
        if isinstance(contains_raw, str):
            candidates = [contains_raw]
        elif isinstance(contains_raw, list):
            candidates = [str(x).strip() for x in contains_raw if str(x).strip()]
        else:
            candidates = []
        if not candidates:
            return bool(str(result.text or "").strip())

        text = str(result.text or "").strip().lower()
        if any(candidate.lower() in text for candidate in candidates):
            return True
        threshold_raw = rule.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.8
        except (TypeError, ValueError):
            threshold = 0.8
        return fuzzy_match(text, candidates, threshold=threshold) is not None

    async def verify_tab_active_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
    ) -> bool:
        region = str(rule.get("tab_active") or "").strip()
        if not region:
            return False
        overlay_rule: dict[str, Any] = {
            "name": f"navigator.verify.{region}.active",
            "region": region,
            "isTabActive": True,
        }
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                self._repo_root,
                [overlay_rule],
                state_flat=state_flat,
            )
        except Exception:
            logger.debug("Navigator: tab-active verify failed for %s", region, exc_info=True)
            return False
        row = out.get(str(overlay_rule["name"]))
        return bool(isinstance(row, dict) and row.get("matched"))

    async def verify_from_screen_rule(
        self,
        rule: dict[str, Any],
        *,
        instance_id: str | None,
    ) -> bool:
        """True when the most-recent screen_history entry matches ``from_screen``.

        ``_wait_for_screen_verified`` runs BEFORE ``_write_screen(target)``, so
        at evaluation time index 0 of the history is the source screen of the
        hop we just took. Only that immediate predecessor is checked: a wider
        window would let an unrelated intermediate hop "validate" a wiki popup
        that we never actually opened.
        """
        if not instance_id:
            return False
        accepted_raw = rule.get("from_screen")
        accepted: list[str] = []
        if isinstance(accepted_raw, list):
            accepted = [str(x).strip() for x in accepted_raw if str(x).strip()]
        elif accepted_raw is not None and str(accepted_raw).strip():
            accepted = [str(accepted_raw).strip()]
        if not accepted:
            return False
        history = await self._screen_state.screen_history(instance_id)
        prev = history[0] if history else ""
        return prev in accepted

    async def verify_rule(
        self,
        image: np.ndarray,
        rule: dict[str, Any],
        *,
        state_flat: dict[str, Any] | None = None,
        instance_id: str | None = None,
    ) -> bool:
        if "from_screen" in rule:
            return await self.verify_from_screen_rule(rule, instance_id=instance_id)
        checks: list[bool] = []
        if "match" in rule:
            checks.append(await self.verify_match_rule(image, rule, state_flat=state_flat))
        if "ocr" in rule:
            checks.append(await self.verify_ocr_rule(image, rule, state_flat=state_flat))
        if "tab_active" in rule:
            checks.append(
                await self.verify_tab_active_rule(image, rule, state_flat=state_flat)
            )
        return bool(checks) and all(checks)
