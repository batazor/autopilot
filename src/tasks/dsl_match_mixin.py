"""Match-related methods for ``DslScenarioTask`` (template match + red-dot filter).

Pulled out of ``tasks/dsl_scenario.py`` so that file stays readable. The mixin
relies on these instance attributes provided by the host class:

- ``redis_client`` — async redis or ``None``
- ``_last_match_region`` / ``_last_match_row`` — sticky state used by the
  click executor to tap matched coords on the same region
- ``_exclude_match_top_lefts`` — per-region list of already-clicked top-lefts
  so ``while_match`` can skip duplicates
- ``_state_flat()`` — flat per-player state dict for version-aware lookups

External callers should still import ``DslScenarioTask`` from
``tasks.dsl_scenario``; this module is internal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from adb import BotActions
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.tab_active_detector import (
    TAB_ACTIVE_MAX_MEAN_SATURATION,
    TAB_ACTIVE_MIN_MEAN_VALUE,
    is_tab_active_in_bbox_percent,
)
from layout.white_border_detector import (
    WHITE_BORDER_MAX_MEAN_SATURATION,
    WHITE_BORDER_MIN_MEAN_VALUE,
    find_white_border_match_in_search_roi,
    has_white_border_in_bbox_percent,
)
from layout.yellow_glow_detector import find_yellow_glow_squares
from tasks.dsl_scenario_helpers import (
    _step_red_dot_requirement,
    _step_tab_active_requirement,
    _step_white_border_requirement,
    _step_yellow_glow_requirement,
)

logger = logging.getLogger(__name__)


class DslMatchMixin(_Base):
    redis_client: Any
    _last_match_region: str
    _last_match_row: dict[str, Any] | None
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]]

    def _state_flat(self) -> dict[str, Any] | None: ...  # provided by host

    async def _persist_dsl_last_match(
        self,
        instance_id: str,
        *,
        region: str,
        threshold: float,
        row: dict[str, Any] | None,
        detail: str = "",
    ) -> None:
        """Expose last template ``match`` outcome on instance Redis hash for Click approvals UI."""
        if self.redis_client is None:
            return
        detail_s = (detail or "").strip()
        if not detail_s and isinstance(row, dict):
            # Overlay sets ``reason`` when a post-threshold gate fails (e.g. low_bright_detail_ratio).
            detail_s = str(row.get("reason") or "").strip()
        thr_s = f"{float(threshold):.6g}"
        score_s = ""
        matched_s = ""
        if isinstance(row, dict):
            sc = row.get("score")
            score_s = "" if sc is None else str(sc)
            matched_s = "1" if bool(row.get("matched")) else "0"
        # Pre-fill every mode-specific field with "" so a switch between match
        # modes (e.g. findIcon → white_border on the same region) doesn't leave
        # zombie values from the previous mode in the Redis hash. Without this,
        # ``_persist_*`` was effectively merge-only (Redis ``HSET``), so a
        # red_dot/tab_active/white_border row would persist its own fields but
        # silently inherit ``top_left`` / ``template_w`` / ``search_region``
        # from whatever findIcon ran just before — confusing for the approvals
        # UI and any payload diagnostic.
        mapping = {
            "dsl_last_match_region": region,
            "dsl_last_match_threshold": thr_s,
            "dsl_last_match_score": score_s,
            "dsl_last_match_matched": matched_s,
            "dsl_last_match_detail": detail_s,
            "dsl_last_match_at": str(time.time()),
            # Keep the live task card/approval context on the actual DSL
            # probe. Overlay-triggered tasks start with the trigger region
            # (for example `mail.new`), but once the scenario is running the
            # useful region is the current `match` / `while_match` target.
            "current_task_region": region,
            "current_task_threshold": thr_s,
            "current_task_score": score_s,
            "current_task_match_top_left_x": "",
            "current_task_match_top_left_y": "",
            "current_task_template_w": "",
            "current_task_template_h": "",
            "current_task_tap_match_x_pct": "",
            "current_task_tap_match_y_pct": "",
            "dsl_last_match_mode": "",
            "dsl_last_match_red_dot_present": "",
            "dsl_last_match_red_dot_required": "",
            "dsl_last_match_tab_active": "",
            "dsl_last_match_tab_active_required": "",
            "dsl_last_match_white_border_present": "",
            "dsl_last_match_white_border_required": "",
            "dsl_last_match_top_left_x": "",
            "dsl_last_match_top_left_y": "",
            "dsl_last_match_template_w": "",
            "dsl_last_match_template_h": "",
            "dsl_last_match_search_region": "",
            "dsl_last_match_tap_x_pct": "",
            "dsl_last_match_tap_y_pct": "",
            "dsl_last_match_tap_match_x_pct": "",
            "dsl_last_match_tap_match_y_pct": "",
        }
        if isinstance(row, dict):
            # Persist the *kind* of match so the approvals UI knows whether
            # to show "live crop vs template" (template match) or a
            # red-dot / tab-active outcome (state check; template view is
            # irrelevant). Comes straight from the row builder: findIcon /
            # red_dot / tab_active / color_check / text.
            action_s = str(row.get("action") or "").strip()
            if action_s:
                mapping["dsl_last_match_mode"] = action_s
            if "red_dot_present" in row:
                mapping["dsl_last_match_red_dot_present"] = (
                    "1" if bool(row.get("red_dot_present")) else "0"
                )
            if "red_dot_required" in row:
                mapping["dsl_last_match_red_dot_required"] = (
                    "1" if bool(row.get("red_dot_required")) else "0"
                )
            if "tab_active" in row:
                mapping["dsl_last_match_tab_active"] = (
                    "1" if bool(row.get("tab_active")) else "0"
                )
            if "tab_active_required" in row:
                mapping["dsl_last_match_tab_active_required"] = (
                    "1" if bool(row.get("tab_active_required")) else "0"
                )
            if "white_border_present" in row:
                mapping["dsl_last_match_white_border_present"] = (
                    "1" if bool(row.get("white_border_present")) else "0"
                )
            if "white_border_required" in row:
                mapping["dsl_last_match_white_border_required"] = (
                    "1" if bool(row.get("white_border_required")) else "0"
                )
            tl = row.get("top_left")
            tw = row.get("template_w")
            th = row.get("template_h")
            sr = row.get("search_region")
            txp = row.get("tap_x_pct")
            typ = row.get("tap_y_pct")
            tmx = row.get("tap_match_x_pct")
            tmy = row.get("tap_match_y_pct")
            if isinstance(tl, (list, tuple)) and len(tl) >= 2:
                with suppress(TypeError, ValueError):
                    x_s = str(int(float(tl[0])))
                    mapping["dsl_last_match_top_left_x"] = x_s
                    mapping["current_task_match_top_left_x"] = x_s
                with suppress(TypeError, ValueError):
                    y_s = str(int(float(tl[1])))
                    mapping["dsl_last_match_top_left_y"] = y_s
                    mapping["current_task_match_top_left_y"] = y_s
            if tw is not None:
                with suppress(TypeError, ValueError):
                    tw_s = str(int(tw))
                    mapping["dsl_last_match_template_w"] = tw_s
                    mapping["current_task_template_w"] = tw_s
            if th is not None:
                with suppress(TypeError, ValueError):
                    th_s = str(int(th))
                    mapping["dsl_last_match_template_h"] = th_s
                    mapping["current_task_template_h"] = th_s
            if sr is not None and str(sr).strip():
                mapping["dsl_last_match_search_region"] = str(sr).strip()
            if txp is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_x_pct"] = f"{float(txp):.6g}"
            if typ is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_y_pct"] = f"{float(typ):.6g}"
            if tmx is not None:
                with suppress(TypeError, ValueError):
                    tmx_s = f"{float(tmx):.6g}"
                    mapping["dsl_last_match_tap_match_x_pct"] = tmx_s
                    mapping["current_task_tap_match_x_pct"] = tmx_s
            if tmy is not None:
                with suppress(TypeError, ValueError):
                    tmy_s = f"{float(tmy):.6g}"
                    mapping["dsl_last_match_tap_match_y_pct"] = tmy_s
                    mapping["current_task_tap_match_y_pct"] = tmy_s
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=mapping)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_match failed", exc_info=True)

    async def _pause_for_while_match_no_iterations_approval(
        self,
        *,
        actions: BotActions,
        instance_id: str,
        scenario_key: str,
        region: str,
        attempts: int,
        interval_s: float,
    ) -> bool:
        """In approval mode, publish a diagnostic pause before strict while_match retry."""
        # Lazy import via the main module so existing monkeypatches against
        # ``tasks.dsl_scenario`` (set by tests) take effect on these helpers.
        from tasks import dsl_scenario as _dsl

        if not _dsl.click_approval_enabled(instance_id):
            return True

        if self.redis_client is not None:
            with suppress(Exception):
                await self.redis_client.hset(
                    f"wos:instance:{instance_id}:state",
                    mapping={
                        "current_task_region": region,
                        "current_scenario": scenario_key,
                    },
                )

        approval_payload: dict[str, object] = {
            "type": "diagnostic",
            "region": region,
            "diagnostic": "while_match_no_iterations",
            "attempts": int(attempts),
            "interval": float(interval_s),
        }
        attach_preview = getattr(actions, "attach_approval_preview", None)
        if callable(attach_preview):
            with suppress(Exception):
                await asyncio.to_thread(attach_preview, instance_id, approval_payload)

        ok, req_id = await asyncio.to_thread(
            _dsl._require_approval, instance_id, approval_payload
        )
        if req_id is not None:
            with suppress(Exception):
                _dsl._redis().delete(f"wos:ui:click_approval:current:{instance_id}")
                _dsl._redis().delete(f"wos:ui:click_approval:response:{req_id}")
        if not ok:
            logger.info(
                "dsl_scenario: while_match no_iterations rejected — aborting scenario %s",
                _scen(scenario_key),
            )
        return ok

    async def _match_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        repo_root: Path,
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> dict[str, Any] | None:
        pair = (
            screen_region_by_name(
                area_doc,
                region,
                state_flat=self._state_flat(),
            )
            if region
            else None
        )
        if pair is None:
            logger.warning("dsl_scenario: match region not found in area.json: %s", region)
            await self._persist_dsl_last_match(
                instance_id,
                region=region,
                threshold=0.9,
                row=None,
                detail="region_not_found_in_area",
            )
            return None
        raw_threshold = step.get("threshold")
        if raw_threshold is None:
            raw_threshold = pair[1].get("threshold", 0.9)
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            threshold = 0.9

        red_dot_req = _step_red_dot_requirement(step)
        tab_active_req = _step_tab_active_requirement(step)
        white_border_req = _step_white_border_requirement(step)
        yellow_glow_req = _step_yellow_glow_requirement(step)

        # Red-dot-only short-circuit: when the step carries ``isRedDot: true|false``
        # the user is asking "is there a red dot in <region>?" — they do NOT
        # care about template/OCR identity match, so skip the heavy match path
        # entirely. This avoids stale-crop ``shape_mismatch`` failures and
        # works on any region with ``has_red_dot: true`` in area.json (no crop
        # PNG required).
        # Use the cached framebuffer when ``BotActions`` exposes it — sibling
        # ``while_match`` / ``match`` probes share the same screen state until a
        # tap/swipe invalidates the cache, so we'd otherwise re-screencap the
        # same pixels N times for a multi-region scenario like ``claim_trials``.
        # Tests that pass a ``_FakeActions`` without the cached helper transparently
        # fall back to the always-fresh ``capture_screen_bgr``.
        capture = getattr(actions, "capture_screen_bgr_cached", actions.capture_screen_bgr)

        if red_dot_req is not None:
            image_bgr = await asyncio.to_thread(capture, instance_id)
            row = self._build_red_dot_only_row(
                region=region,
                region_def=pair[1],
                image_bgr=image_bgr,
                requirement=red_dot_req,
            )
        elif tab_active_req is not None:
            image_bgr = await asyncio.to_thread(capture, instance_id)
            row = self._build_tab_active_only_row(
                region=region,
                region_def=pair[1],
                image_bgr=image_bgr,
                requirement=tab_active_req,
                step=step,
            )
        elif white_border_req is not None:
            image_bgr = await asyncio.to_thread(capture, instance_id)
            row = self._build_white_border_only_row(
                region=region,
                region_def=pair[1],
                image_bgr=image_bgr,
                requirement=white_border_req,
                step=step,
            )
        elif yellow_glow_req is not None:
            image_bgr = await asyncio.to_thread(capture, instance_id)
            row = self._build_yellow_glow_only_row(
                region=region,
                region_def=pair[1],
                image_bgr=image_bgr,
                requirement=yellow_glow_req,
            )
        else:
            # `match:` / `while_match:` should evaluate using the region's action from `area.json`.
            # Historically it always used `findIcon`, which breaks color-only regions (e.g. `isWorkers`).
            area_action = str(pair[1].get("action") or "").strip()
            if area_action not in {"exist", "text", "color_check", "findIcon"}:
                # `click` (and other non-detection actions) cannot be matched; default to `exist`.
                area_action = "exist"

            rule: dict[str, Any] = {
                "name": f"dsl.{scenario_key}.{region}.visible",
                "region": region,
                "action": area_action,
                "threshold": threshold,
            }
            if area_action == "color_check":
                # Color label: prefer step override, else inherit from area.json.
                rule["type"] = str(step.get("type") or pair[1].get("type") or "").strip()
            if area_action == "text":
                # ``expected`` on the DSL step gates fuzzy OCR matching in
                # ``overlay_engine`` (score >= ``threshold``). Without
                # ``expected`` the text branch falls back to ``matched =
                # bool(txt)`` on the primary bbox alone.
                expected = step.get("expected")
                if isinstance(expected, list) and expected:
                    rule["expected"] = [str(x) for x in expected]
                elif isinstance(expected, str) and expected.strip():
                    rule["expected"] = [expected]
            # When a region has multiple identical icons (mail list), avoid re-hitting the same one.
            excl = self._exclude_match_top_lefts.get(region)
            if excl:
                rule["exclude_top_lefts"] = [[x, y] for (x, y) in excl[-6:]]
                rule["exclude_radius_px"] = 24
            min_sat = step.get("min_match_saturation")
            if min_sat is not None:
                rule["min_match_saturation"] = min_sat
            template = str(step.get("template") or "").replace("\\", "/").strip()
            if template:
                rule["template"] = template
            search_region = str(step.get("search_region") or "").strip()
            if search_region:
                rule["search_region"] = search_region
            # Lazy import via main module so monkeypatches against
            # ``tasks.dsl_scenario.evaluate_overlay_rules_async`` apply here too.
            from tasks import dsl_scenario as _dsl

            image_bgr = await asyncio.to_thread(capture, instance_id)
            out = await _dsl.evaluate_overlay_rules_async(
                image_bgr, area_doc, repo_root, [rule], state_flat=self._state_flat()
            )
            row = out.get(str(rule["name"]))

        if isinstance(row, dict):
            # Keep last match for subsequent `click:` on the same region.
            self._last_match_region = region
            self._last_match_row = row
            await self._persist_dsl_last_match(
                instance_id,
                region=region,
                threshold=threshold,
                row=row,
                detail="",
            )
            return row
        await self._persist_dsl_last_match(
            instance_id,
            region=region,
            threshold=threshold,
            row=None,
            detail="no_overlay_row",
        )
        if self._last_match_region == region:
            self._last_match_region = ""
            self._last_match_row = None
        return None

    @staticmethod
    def _build_red_dot_only_row(
        *,
        region: str,
        region_def: dict[str, Any],
        image_bgr: Any,
        requirement: bool,
    ) -> dict[str, Any]:
        """Build a match row from the red-dot detector alone (no template match).

        Used by ``match:`` / ``while_match:`` steps that carry ``isRedDot:`` —
        the row populates ``tap_x_pct`` / ``tap_y_pct`` from the bbox center so
        a follow-up ``click:`` on the same region still has coords.
        """
        base: dict[str, Any] = {
            "matched": False,
            "action": "red_dot",
            "region": region,
            "red_dot_required": bool(requirement),
        }
        if not bool(region_def.get("has_red_dot")):
            base["reason"] = "red_dot_capability_disabled"
            return base
        bbox = region_def.get("bbox") if isinstance(region_def.get("bbox"), dict) else None
        if bbox is None:
            base["reason"] = "missing_bbox_for_red_dot"
            return base

        # Strict-bbox search: when a region declares ``has_red_dot: true`` the
        # author already drew the rectangle around where the badge appears, so
        # the detector should honour that exact rectangle — no upward padding
        # for unread-counter overflow, no sideways pad for edge taps. Mirrors
        # the overlay engine's red_dot handler. Without this, a wipe of the
        # labeled bbox in tests still triggers on neighbouring badges and the
        # ``while_match`` loop never exits.
        present = bool(
            has_red_dot_in_bbox_percent(
                image_bgr, bbox, pad_px=0, edge_badge_pad_ratio=0.0
            )
        )
        base["red_dot_present"] = present
        if present != bool(requirement):
            base["reason"] = "red_dot_missing" if requirement else "red_dot_unexpected"
            return base

        base["matched"] = True
        try:
            cx = float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0
            cy = float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0
        except (TypeError, ValueError):
            cx = cy = 0.0
        base["tap_x_pct"] = cx
        base["tap_y_pct"] = cy
        base["tap_match_x_pct"] = cx
        base["tap_match_y_pct"] = cy
        return base

    @staticmethod
    def _build_yellow_glow_only_row(
        *,
        region: str,
        region_def: dict[str, Any],
        image_bgr: Any,
        requirement: bool,
    ) -> dict[str, Any]:
        """Build a match row for ``isYellowGlow:`` steps.

        Scopes :func:`find_yellow_glow_squares` to candidates whose centre
        falls inside the region's bbox — the region acts as the search ROI,
        so the same step works whether the column has 1, 4 or N tiles. The
        first (lowest-fill) claim wins; its centre is exposed as the tap
        target along with ``template_w`` / ``template_h`` so the DSL click
        path lands a randomised tap *inside* the tile, not on the column's
        bbox centre.
        """
        base: dict[str, Any] = {
            "matched": False,
            "action": "yellow_glow",
            "region": region,
            "yellow_glow_required": bool(requirement),
        }
        bbox = region_def.get("bbox") if isinstance(region_def.get("bbox"), dict) else None
        if bbox is None:
            base["reason"] = "missing_bbox_for_yellow_glow"
            return base

        rx = float(bbox.get("x") or 0.0)
        ry = float(bbox.get("y") or 0.0)
        rw = float(bbox.get("width") or 0.0)
        rh = float(bbox.get("height") or 0.0)

        squares = find_yellow_glow_squares(image_bgr)
        # Restrict to candidates whose centre sits inside the region bbox.
        in_region = []
        for sq in squares:
            scx = sq.bbox_percent["x"] + sq.bbox_percent["width"] / 2.0
            scy = sq.bbox_percent["y"] + sq.bbox_percent["height"] / 2.0
            if rx <= scx <= rx + rw and ry <= scy <= ry + rh:
                in_region.append(sq)
        present = bool(in_region)
        base["yellow_glow_present"] = present
        base["yellow_glow_count"] = len(in_region)
        if present != bool(requirement):
            base["reason"] = "yellow_glow_missing" if requirement else "yellow_glow_unexpected"
            return base

        base["matched"] = True
        if in_region:
            # Tap target = centre of the lowest-fill (clearest hollow rim)
            # candidate; ``find_yellow_glow_squares`` already sorts by
            # fill_ratio ascending so ``in_region[0]`` wins.
            chosen = in_region[0]
            cx = chosen.bbox_percent["x"] + chosen.bbox_percent["width"] / 2.0
            cy = chosen.bbox_percent["y"] + chosen.bbox_percent["height"] / 2.0
            base["tap_x_pct"] = cx
            base["tap_y_pct"] = cy
            base["tap_match_x_pct"] = cx
            base["tap_match_y_pct"] = cy
            # Expose the tile's pixel size so the DSL click path builds a
            # synthetic bbox sized to the tile (not the column) and
            # randomises the tap inside it — mirrors how detectTabs does it.
            img_h, img_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            tw = max(1, int(round(chosen.bbox_percent["width"] / 100.0 * img_w)))
            th = max(1, int(round(chosen.bbox_percent["height"] / 100.0 * img_h)))
            base["template_w"] = tw
            base["template_h"] = th
        else:
            # Region matched (requirement was False, no glow as expected) —
            # tap target falls back to bbox centre for symmetry with siblings.
            cx = rx + rw / 2.0
            cy = ry + rh / 2.0
            base["tap_x_pct"] = cx
            base["tap_y_pct"] = cy
            base["tap_match_x_pct"] = cx
            base["tap_match_y_pct"] = cy
        return base

    @staticmethod
    def _build_tab_active_only_row(
        *,
        region: str,
        region_def: dict[str, Any],
        image_bgr: Any,
        requirement: bool,
        step: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a match row from the tab_active detector alone (no template match).

        Used by ``match:`` / ``while_match:`` steps that carry ``isTabActive:`` —
        the row populates ``tap_x_pct`` / ``tap_y_pct`` from the bbox center so a
        follow-up ``click:`` on the same region still has coords.
        """
        base: dict[str, Any] = {
            "matched": False,
            "action": "tab_active",
            "region": region,
            "tab_active_required": bool(requirement),
        }
        bbox = region_def.get("bbox") if isinstance(region_def.get("bbox"), dict) else None
        if bbox is None:
            base["reason"] = "missing_bbox_for_tab_active"
            return base

        max_s = TAB_ACTIVE_MAX_MEAN_SATURATION
        min_v = TAB_ACTIVE_MIN_MEAN_VALUE
        if isinstance(step, dict):
            with suppress(TypeError, ValueError):
                if step.get("max_mean_saturation") is not None:
                    max_s = float(step["max_mean_saturation"])
            with suppress(TypeError, ValueError):
                if step.get("min_mean_value") is not None:
                    min_v = float(step["min_mean_value"])

        active = bool(
            is_tab_active_in_bbox_percent(
                image_bgr,
                bbox,
                max_mean_saturation=max_s,
                min_mean_value=min_v,
            )
        )
        base["tab_active"] = active
        if active != bool(requirement):
            base["reason"] = "tab_inactive" if requirement else "tab_active_unexpected"
            return base

        base["matched"] = True
        try:
            cx = float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0
            cy = float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0
        except (TypeError, ValueError):
            cx = cy = 0.0
        base["tap_x_pct"] = cx
        base["tap_y_pct"] = cy
        base["tap_match_x_pct"] = cx
        base["tap_match_y_pct"] = cy
        return base

    @staticmethod
    def _build_white_border_only_row(
        *,
        region: str,
        region_def: dict[str, Any],
        image_bgr: Any,
        requirement: bool,
        step: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a match row from the white-border detector alone (no template match).

        Used by ``match:`` / ``while_match:`` steps that carry ``isWhiteBorder:``.
        Two passes:

        1. **Slide-find** via :func:`find_white_border_match_in_search_roi` —
           contour-based search for a closed near-white rectangle inside the
           region's bbox. Handles the case where the labeled bbox is a
           *search zone* and the highlighted item lives somewhere inside it
           (e.g., ``button.claim`` in the trial-box popup, where the actual
           claim button position varies between popups).

        2. **Halo fallback** via :func:`has_white_border_in_bbox_percent` —
           tests halo statistics around the labeled bbox itself. Used when
           the contour pass returns no candidate; this is the path that
           works for fixed-position icons where the labeled bbox IS the icon
           (the VIP Point Rewards reward tiles in tests).

        Either pass: ``tap_x_pct`` / ``tap_y_pct`` is set to the found
        location's center so a follow-up ``click:`` on the same region taps
        the actual highlighted item, not just the labeled bbox center.
        """
        logger.info(
            "white_border check: region=%s required=%s (entered _build_white_border_only_row)",
            region, requirement,
        )
        base: dict[str, Any] = {
            "matched": False,
            "action": "white_border",
            "region": region,
            "white_border_required": bool(requirement),
        }
        bbox = region_def.get("bbox") if isinstance(region_def.get("bbox"), dict) else None
        if bbox is None:
            logger.info(
                "white_border check: region=%s aborted — region has no bbox in area.json",
                region,
            )
            base["reason"] = "missing_bbox_for_white_border"
            return base

        # --- Pass 1: slide-find (contour-based) ---
        # Try the primary bbox only. Movable template regions are handled by
        # the `isSearch` full-frame matcher before a click consumes the match.
        contour_kwargs: dict[str, Any] = {}
        if isinstance(step, dict):
            int_keys = (
                "near_white_min_value",
                "near_white_max_saturation",
                "morph_close_kernel",
                "min_side_px",
                "max_side_px",
                "merge_gap_px",
            )
            float_keys = (
                "min_aspect",
                "max_aspect",
                "max_fill_ratio",
                "min_interior_saturation",
            )
            for key in int_keys:
                with suppress(TypeError, ValueError):
                    if step.get(key) is not None:
                        contour_kwargs[key] = int(step[key])
            for key in float_keys:
                with suppress(TypeError, ValueError):
                    if step.get(key) is not None:
                        contour_kwargs[key] = float(step[key])
        match = find_white_border_match_in_search_roi(
            image_bgr,
            bbox,
            **contour_kwargs,
        )
        match_source = "primary"
        if match is not None:
            base["white_border_present"] = True
            x, y, w, h = match["px_rect"]  # type: ignore[index]  # ty: ignore[not-iterable]
            base["top_left"] = [int(x), int(y)]
            base["template_w"] = int(w)
            base["template_h"] = int(h)
            cx = float(match["cx_pct"])  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            cy = float(match["cy_pct"])  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            inner_s = float(match.get("interior_saturation") or 0.0)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            logger.info(
                "white_border slide-find: region=%s source=%s contour=(%d,%d,%dx%d) "
                "center=(%.2f%%,%.2f%%) inner_S=%.0f required=%s",
                region, match_source, int(x), int(y), int(w), int(h),
                cx, cy, inner_s, requirement,
            )
            if not bool(requirement):
                base["reason"] = "white_border_unexpected"
                return base
            base["matched"] = True
            base["tap_x_pct"] = cx
            base["tap_y_pct"] = cy
            base["tap_match_x_pct"] = cx
            base["tap_match_y_pct"] = cy
            base["search_source"] = match_source
            return base

        # --- Pass 2: halo fallback (existing behavior) ---
        max_s = WHITE_BORDER_MAX_MEAN_SATURATION
        min_v = WHITE_BORDER_MIN_MEAN_VALUE
        if isinstance(step, dict):
            with suppress(TypeError, ValueError):
                if step.get("max_mean_saturation") is not None:
                    max_s = float(step["max_mean_saturation"])
            with suppress(TypeError, ValueError):
                if step.get("min_mean_value") is not None:
                    min_v = float(step["min_mean_value"])

        present = bool(
            has_white_border_in_bbox_percent(
                image_bgr,
                bbox,
                max_mean_saturation=max_s,
                min_mean_value=min_v,
            )
        )
        base["white_border_present"] = present
        logger.info(
            "white_border halo fallback: region=%s present=%s required=%s "
            "(slide-find returned no contour candidates)",
            region, present, requirement,
        )
        if present != bool(requirement):
            base["reason"] = (
                "white_border_missing" if requirement else "white_border_unexpected"
            )
            return base

        base["matched"] = True
        try:
            cx = float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0
            cy = float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0
        except (TypeError, ValueError):
            cx = cy = 0.0
        base["tap_x_pct"] = cx
        base["tap_y_pct"] = cy
        base["tap_match_x_pct"] = cx
        base["tap_match_y_pct"] = cy
        return base
