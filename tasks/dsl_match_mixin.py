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
from pathlib import Path
from typing import Any

from actions.tap import BotActions
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from tasks.dsl_scenario_helpers import _step_red_dot_requirement

logger = logging.getLogger(__name__)


class DslMatchMixin:
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
        mapping = {
            "dsl_last_match_region": region,
            "dsl_last_match_threshold": thr_s,
            "dsl_last_match_score": score_s,
            "dsl_last_match_matched": matched_s,
            "dsl_last_match_detail": detail_s,
            "dsl_last_match_at": str(time.time()),
        }
        if isinstance(row, dict):
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
                    mapping["dsl_last_match_top_left_x"] = str(int(float(tl[0])))
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_top_left_y"] = str(int(float(tl[1])))
            if tw is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_template_w"] = str(int(tw))
            if th is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_template_h"] = str(int(th))
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
                    mapping["dsl_last_match_tap_match_x_pct"] = f"{float(tmx):.6g}"
            if tmy is not None:
                with suppress(TypeError, ValueError):
                    mapping["dsl_last_match_tap_match_y_pct"] = f"{float(tmy):.6g}"
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
            "source": {
                "component": "tasks.dsl_scenario.DslScenarioTask",
                "note": "while_match matched zero times; approve to retry later, reject to stop",
            },
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
        pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat()) if region else None
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

        # Red-dot-only short-circuit: when the step carries ``isRedDot: true|false``
        # the user is asking "is there a red dot in <region>?" — they do NOT
        # care about template/OCR identity match, so skip the heavy match path
        # entirely. This avoids stale-crop ``shape_mismatch`` failures and
        # works on any region with ``has_red_dot: true`` in area.json (no crop
        # PNG required).
        if red_dot_req is not None:
            image_bgr = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
            row = self._build_red_dot_only_row(
                region=region,
                region_def=pair[1],
                image_bgr=image_bgr,
                requirement=red_dot_req,
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
            # When a region has multiple identical icons (mail list), avoid re-hitting the same one.
            excl = self._exclude_match_top_lefts.get(region)
            if excl:
                rule["exclude_top_lefts"] = [[x, y] for (x, y) in excl[-6:]]
                rule["exclude_radius_px"] = 24
            min_sat = step.get("min_match_saturation")
            if min_sat is not None:
                rule["min_match_saturation"] = min_sat
            # Lazy import via main module so monkeypatches against
            # ``tasks.dsl_scenario.evaluate_overlay_rules_async`` apply here too.
            from tasks import dsl_scenario as _dsl

            image_bgr = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
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

        present = bool(has_red_dot_in_bbox_percent(image_bgr, bbox))
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

