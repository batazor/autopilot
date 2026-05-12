"""Inline / nested DSL step execution for :class:`tasks.dsl_scenario.DslScenarioTask`.

Holds navigation, tap geometry, color-check (match-time), and ``_run_inline_step``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import cv2

from actions.tap import BotActions
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.template_match import (
    patch_bgr_from_bbox_percent,
    validate_live_bbox_patch_vs_reference_dims,
)
from layout.types import Point
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import (
    _COLOR_WORD_ALIASES,
    _BreakRepeat,
    _dsl_cond_allows_step,
    _enqueue_scenario,
    _parse_wait_seconds,
    _read_current_screen,
    _repo_root,
)

logger = logging.getLogger(__name__)


class DslScenarioInlineMixin:
    """Navigation + per-step actions shared by top-level and nested DSL blocks."""

    redis_client: Any | None
    player_id: str | None
    priority: int
    scenario_key: str
    tap_region: str
    tap_x_pct: float | None
    tap_y_pct: float | None
    _last_match_region: str
    _last_match_row: dict[str, Any] | None
    _last_tap_region_clicked: str
    _implicit_match_for_region: str
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]]

    async def _navigate_to_node(
        self,
        instance_id: str,
        target_node: str,
        *,
        actions: Any,
        scenario_key: str,
    ) -> bool:
        """Drive the FSM to ``target_node`` via :class:`Navigator` (BFS over screen_graph).

        No-op when ``current_screen`` already equals the target. Unknown / not-in-graph
        targets are treated as soft failures (logged, scenario aborts).
        """
        from navigation.detector import ScreenName
        from navigation.navigator import Navigator

        target_node = target_node.strip()
        if not target_node:
            return True
        try:
            target = ScreenName(target_node)
        except ValueError:
            logger.warning(
                "dsl_scenario: unknown FSM screen %r for scenario %s — skipping navigation",
                target_node,
                _scen(scenario_key),
            )
            return False

        cur = await _read_current_screen(instance_id, self.redis_client)
        if cur == str(target):
            return True

        await self._write_step_context(instance_id, scenario=scenario_key)
        navigator = Navigator(
            actions.capture_screen_bgr,
            actions.tap,
            redis_client=self.redis_client,
        )
        ok = await navigator.navigate_to(target, instance_id)
        if not ok:
            logger.warning(
                "dsl_scenario: navigation to %s failed (scenario=%s instance=%s)",
                target_node,
                _scen(scenario_key),
                instance_id,
            )
            return False
        if self.redis_client is not None:
            try:
                await self.redis_client.hset(
                    f"wos:instance:{instance_id}:state",
                    "current_screen",
                    str(target),
                )
            except Exception:
                logger.debug("dsl_scenario: failed to persist current_screen", exc_info=True)
        return True

    def estimate_duration(self) -> int:
        return 15

    async def _run_exec_step(self, name: str, instance_id: str) -> None:
        """Dispatch ``exec: <name>`` to :data:`tasks.dsl_exec.DSL_EXEC_REGISTRY`."""
        from tasks.dsl_exec import DSL_EXEC_REGISTRY, DslExecContext

        fn = DSL_EXEC_REGISTRY.get(name)
        if fn is None:
            logger.warning("dsl_scenario: unknown exec step %r", name)
            return
        ctx = DslExecContext(
            redis_client=self.redis_client,
            player_id=self.player_id,
            instance_id=instance_id,
        )
        try:
            await fn(ctx)
        except Exception:
            logger.exception("dsl_scenario: exec %r failed", name)

    async def _color_check_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> bool:
        """Check dominant color inside a named region.

        Note: the DSL no longer has a dedicated `color_check:` step. Color checks are evaluated
        via `match: <region>` when the region in `area.json` uses `action: color_check`.
        """
        raw_want = str(step.get("type") or "").strip().lower()
        want = _COLOR_WORD_ALIASES.get(raw_want, raw_want)
        threshold_raw = step.get("threshold")
        try:
            threshold = float(threshold_raw) if threshold_raw is not None else 0.50
        except (TypeError, ValueError):
            threshold = 0.50
        threshold = max(0.0, min(1.0, threshold))

        _sf = self._state_flat()
        pair = (
            screen_region_by_name(area_doc, region, state_flat=_sf) if region else None
        )
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "region_not_found",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        reg_def = pair[1]
        if not want:
            want2 = str(reg_def.get("type") or "").strip().lower()
            want = _COLOR_WORD_ALIASES.get(want2, want2)

        if want not in {"red", "blue", "gray", "green"}:
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "invalid_type",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for color_check (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "capture_failed",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        bbox = reg_def["bbox"]
        if not isinstance(bbox, dict):
            await self._persist_dsl_last_color(
                instance_id,
                {
                    "dsl_last_color_region": region,
                    "dsl_last_color_status": "invalid_bbox",
                    "dsl_last_color_want": want,
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": f"{threshold:.3f}",
                },
            )
            return False

        repo_root = _repo_root()
        patch, _tl = patch_bgr_from_bbox_percent(image, bbox)
        ph, pw = int(patch.shape[0]), int(patch.shape[1])
        resolved_region = str(reg_def.get("name") or "").strip() or region
        ref_rel = effective_ocr_for_region(pair[0], reg_def)
        if ref_rel:
            crop_path = exported_crop_png(repo_root, ref_rel, resolved_region)
            if crop_path.is_file():
                ref_img = cv2.imread(str(crop_path))
                if ref_img is not None and ref_img.size > 0:
                    ref_ph, ref_pw = int(ref_img.shape[0]), int(ref_img.shape[1])
                    try:
                        validate_live_bbox_patch_vs_reference_dims(
                            pw, ph, ref_pw, ref_ph, reference_label="exported crop"
                        )
                    except ValueError as exc:
                        await self._persist_dsl_last_color(
                            instance_id,
                            {
                                "dsl_last_color_region": region,
                                "dsl_last_color_status": "crop_size_mismatch",
                                "dsl_last_color_want": want,
                                "dsl_last_color_dominant": "",
                                "dsl_last_color_share": "",
                                "dsl_last_color_threshold": f"{threshold:.3f}",
                                "dsl_last_color_detail": str(exc),
                            },
                        )
                        return False

        dominant, shares = dominant_color_label_bgr(patch)
        share = float(shares.get(dominant, 0.0))
        ok = dominant == want and share >= threshold

        await self._persist_dsl_last_color(
            instance_id,
            {
                "dsl_last_color_region": region,
                "dsl_last_color_status": "ok" if ok else "mismatch",
                "dsl_last_color_want": want,
                "dsl_last_color_dominant": dominant,
                "dsl_last_color_share": f"{share:.3f}",
                "dsl_last_color_threshold": f"{threshold:.3f}",
            },
        )
        return ok

    def _region_has_search_companion(
        self,
        area_doc: dict[str, Any],
        region: str,
    ) -> bool:
        """True if ``<region>_search`` exists in area.json (state-aware lookup)."""
        if not region:
            return False
        return (
            screen_region_by_name(
                area_doc, f"{region}_search", state_flat=self._state_flat()
            )
            is not None
        )

    async def _tap_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        repo_root: Path,
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        region: str,
        step: dict[str, Any] | None = None,
    ) -> TaskResult | None:
        _sf = self._state_flat()
        pair = (
            screen_region_by_name(area_doc, region, state_flat=_sf) if region else None
        )
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning("dsl_scenario: region not found in area.json: %s", region)
            return None

        # When a `<region>_search` ROI exists, the static template bbox is just a sample
        # crop — the icon's real on-screen position is anywhere inside the search ROI.
        # Run an implicit `match:` first so `_point_for_region_action` taps the found
        # location instead of the stale bbox center. Skip if the caller already did a
        # `match:` for this region (the recent `_last_match_row` covers it).
        already_matched = (
            self._last_match_region == region and self._last_match_row is not None
        )
        if not already_matched and self._region_has_search_companion(area_doc, region):
            # Forward optional gating from the click step so users can write
            # ``click: foo / threshold: 0.95 / min_match_saturation: 40`` and have
            # the implicit search honor those constraints. ``isRedDot`` and other
            # match-only filters are not meaningful for a tap, but pass-through is
            # cheap (the engine ignores unknown keys).
            await self._match_region(
                actions=actions,
                area_doc=area_doc,
                repo_root=repo_root,
                instance_id=instance_id,
                scenario_key=scenario_key,
                step=step or {},
                region=region,
            )
            self._implicit_match_for_region = region

        pt = self._point_for_region_action(region, pair[1]["bbox"], dev_w, dev_h)
        # The flag is per-tap; clear after consumption so a subsequent
        # explicit ``match:`` controls behaviour again.
        if self._implicit_match_for_region == region:
            self._implicit_match_for_region = ""

        tapped = await asyncio.to_thread(
            actions.tap,
            instance_id,
            pt,
            approval_region=region,
        )
        if not tapped:
            logger.info(
                "dsl_scenario: tap rejected or blocked — aborting scenario %s",
                _scen(scenario_key),
            )
            await self._clear_step_context(instance_id)
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={
                    "scenario": scenario_key,
                    "reason": "tap_not_approved",
                },
            )
        self._last_tap_region_clicked = region
        # After a click on a matched region, remember the last match top-left so the next
        # `while_match` can pick a different occurrence if multiple are present.
        if (
            self._last_match_row is not None
            and self._last_match_region == region
            and isinstance(self._last_match_row.get("top_left"), (list, tuple))
            and len(self._last_match_row.get("top_left")) >= 2  # type: ignore[arg-type]
        ):
            try:
                tl = self._last_match_row.get("top_left")
                x0 = int(float(tl[0]))  # type: ignore[index]
                y0 = int(float(tl[1]))  # type: ignore[index]
                self._exclude_match_top_lefts.setdefault(region, []).append((x0, y0))
            except Exception:
                pass
        return None

    def _point_for_region_action(
        self,
        region: str,
        bbox: dict[str, Any],
        dev_w: int,
        dev_h: int,
    ) -> Point:
        # Prefer coordinates from the latest in-scenario overlay probe (`match` / `while_match`).
        # Queue items may carry `tap_x_pct`/`tap_y_pct` from when overlay enqueued `pushScenario`;
        # those can be a different peak or an older frame than the capture used for this click.
        # For an *implicit* auto-match (search companion + no explicit `match:`), use the engine's
        # best-found position even if the score didn't clear the threshold — the user explicitly
        # asked us to find this button, threshold gating only matters when verifying presence.
        implicit = self._implicit_match_for_region == region
        if (
            self._last_match_row is not None
            and self._last_match_region == region
            and (implicit or bool(self._last_match_row.get("matched")))
            and self._last_match_row.get("tap_x_pct") is not None
            and self._last_match_row.get("tap_y_pct") is not None
        ):
            try:
                txp = float(self._last_match_row.get("tap_x_pct"))  # type: ignore[arg-type]
                typ = float(self._last_match_row.get("tap_y_pct"))  # type: ignore[arg-type]
                return Point(
                    int(round(txp / 100.0 * dev_w)),
                    int(round(typ / 100.0 * dev_h)),
                )
            except Exception:
                pass
        tap_region = str(self.tap_region or "").strip()
        if (
            self.tap_x_pct is not None
            and self.tap_y_pct is not None
            and (not tap_region or tap_region == region)
        ):
            return Point(
                int(round(float(self.tap_x_pct) / 100.0 * dev_w)),
                int(round(float(self.tap_y_pct) / 100.0 * dev_h)),
            )
        return bbox_percent_center_to_device_point(bbox, dev_w, dev_h)

    async def _run_inline_step(
        self,
        step: dict[str, Any],
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        repo_root: Path,
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        trace_path: str = "",
    ) -> TaskResult | None:
        if "break" in step:
            tgt = str(step.get("break") or "").strip().lower()
            if tgt in {"loop", "repeat"}:
                raise _BreakRepeat()
            self._append_trace_row(trace_path, step, "ok")
            return None
        ip = await self._inline_preempt_if_needed(instance_id, scenario_key)
        if ip is not None:
            return ip
        if "long_click" in step:
            region = str(step.get("long_click") or "").strip()
            if not region:
                return None
            # `wait` (or `duration`) is interpreted as long-press duration.
            duration_ms = 800
            raw_dur = step.get("duration")
            if raw_dur is None:
                raw_dur = step.get("wait")
            try:
                dur_s = _parse_wait_seconds(raw_dur)
                if dur_s > 0:
                    duration_ms = int(round(dur_s * 1000.0))
            except Exception:
                duration_ms = 800

            _sf = self._state_flat()
            pair = (
                screen_region_by_name(area_doc, region, state_flat=_sf)
                if region
                else None
            )
            if pair is None:
                logger.warning("dsl_scenario: unknown region %r for long_click", region)
                self._append_trace_row(
                    trace_path, step, "skipped", reason="region_not_found"
                )
                return None
            _entry, reg = pair
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                logger.warning("dsl_scenario: missing bbox for long_click region %r", region)
                self._append_trace_row(
                    trace_path, step, "skipped", reason="bbox_missing"
                )
                return None
            pt = self._point_for_region_action(region, bbox, dev_w, dev_h)
            ok = False
            try:
                ok = bool(
                    await asyncio.to_thread(
                        actions.long_tap,
                        instance_id,
                        pt,
                        duration_ms=duration_ms,
                    )
                )
            except Exception:
                ok = False
            if not ok:
                logger.info(
                    "dsl_scenario: long_click blocked — aborting scenario %s",
                    _scen(scenario_key),
                )
                await self._clear_step_context(instance_id)
                self._append_trace_row(
                    trace_path, step, "stopped", reason="long_click_not_approved"
                )
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata={"scenario": scenario_key, "reason": "long_click_not_approved"},
                )
            self._last_tap_region_clicked = region
            await asyncio.sleep(0.4)
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "click" in step:
            region = str(step.get("click") or "").strip()
            if region:
                result = await self._tap_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=scenario_key,
                    region=region,
                    step=step,
                )
                if result is not None:
                    md = dict(result.metadata or {})
                    self._append_trace_row(
                        trace_path,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return result
                await asyncio.sleep(0.4)
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "repeat" in step:
            spec = step.get("repeat")
            if isinstance(spec, dict):
                try:
                    max_iters = int(spec.get("max", 1))
                except (TypeError, ValueError):
                    max_iters = 1
                inner_steps = spec.get("steps")
                until_match = str(spec.get("until_match") or "").strip()
                until_any = spec.get("until_any_match")
                stop_click_any = bool(spec.get("stop_after_click"))
                stop_click_regs_raw = spec.get("stop_after_click_regions")
            else:
                try:
                    max_iters = int(spec or 1)
                except (TypeError, ValueError):
                    max_iters = 1
                inner_steps = step.get("steps")
                until_match = ""
                until_any = None
                stop_click_any = False
                stop_click_regs_raw = None

            max_iters = max(0, max_iters)
            if not isinstance(inner_steps, list) or not inner_steps:
                return None

            until_any_list: list[str] = []
            if isinstance(until_any, list):
                until_any_list = [str(x or "").strip() for x in until_any if str(x or "").strip()]

            stop_click_regs: set[str] = set()
            if isinstance(stop_click_regs_raw, list):
                stop_click_regs = {
                    str(x or "").strip()
                    for x in stop_click_regs_raw
                    if str(x or "").strip()
                }

            iter_total = 0
            for iter_idx in range(max_iters):
                self._last_tap_region_clicked = ""
                if until_match:
                    row = await self._match_region(
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        scenario_key=scenario_key,
                        step=step,
                        region=until_match,
                    )
                    if row is not None and bool(row.get("matched")):
                        break
                if until_any_list:
                    for reg in until_any_list:
                        row2 = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=scenario_key,
                            step=step,
                            region=reg,
                        )
                        if row2 is not None and bool(row2.get("matched")):
                            self._append_trace_row(
                                trace_path, step, "ok", iterations=iter_total
                            )
                            return None
                iter_path = f"{trace_path}.{iter_idx}" if trace_path else str(iter_idx)
                self._append_trace_row(
                    iter_path, None, "iter", summary=f"iter {iter_idx}"
                )
                iter_total = iter_idx + 1
                try:
                    for inner_idx, inner in enumerate(inner_steps):
                        if not isinstance(inner, dict):
                            continue
                        result = await self._run_inline_step(
                            inner,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=scenario_key,
                            trace_path=f"{iter_path}.{inner_idx}",
                        )
                        if result is not None:
                            return result
                        if self._last_tap_region_clicked and (
                            stop_click_any
                            or (
                                stop_click_regs
                                and self._last_tap_region_clicked in stop_click_regs
                            )
                        ):
                            self._append_trace_row(
                                trace_path, step, "ok", iterations=iter_total
                            )
                            return None
                except _BreakRepeat:
                    self._append_trace_row(
                        trace_path, step, "ok", iterations=iter_total
                    )
                    return None
            self._append_trace_row(trace_path, step, "ok", iterations=iter_total)
            return None
        if "loop" in step:
            spec = step.get("loop")
            if not isinstance(spec, dict):
                return None
            inner_steps = spec.get("steps")
            if not isinstance(inner_steps, list) or not inner_steps:
                return None

            # Loop ``cond:`` is the exit condition — re-evaluated at the top of
            # every iteration, the loop breaks the first time it holds.
            cond_expr_raw = spec.get("cond")
            cond_expr = (
                str(cond_expr_raw).strip()
                if cond_expr_raw is not None and not isinstance(cond_expr_raw, bool)
                else None
            ) or None

            try:
                max_iters = int(spec.get("max", 100))
            except (TypeError, ValueError):
                max_iters = 100
            max_iters = max(0, max_iters)

            ttl_raw = spec.get("ttl")
            ttl_s = _parse_wait_seconds(ttl_raw) if ttl_raw is not None else 0.0
            deadline = (time.monotonic() + ttl_s) if ttl_s > 0 else None

            iter_total = 0
            try:
                for iter_idx in range(max_iters):
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    # ``cond`` is the exit condition: break the moment it
                    # holds. Re-evaluated each iteration so inner OCR / exec
                    # steps can flip state and exit the loop.
                    if cond_expr is not None and await _dsl_cond_allows_step(
                        {"cond": cond_expr},
                        instance_id,
                        self.redis_client,
                        state_flat=self._state_flat(),
                    ):
                        break

                    iter_path = (
                        f"{trace_path}.{iter_idx}" if trace_path else str(iter_idx)
                    )
                    self._append_trace_row(
                        iter_path, None, "iter", summary=f"iter {iter_idx}"
                    )
                    iter_total = iter_idx + 1
                    for inner_idx, inner in enumerate(inner_steps):
                        if not isinstance(inner, dict):
                            continue
                        # Step-level ``cond:`` is evaluated here so individual
                        # inner steps can be conditionally skipped without
                        # blocking the whole loop.
                        if not await _dsl_cond_allows_step(
                            inner,
                            instance_id,
                            self.redis_client,
                            state_flat=self._state_flat(),
                        ):
                            continue
                        result = await self._run_inline_step(
                            inner,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=scenario_key,
                            trace_path=f"{iter_path}.{inner_idx}",
                        )
                        if result is not None:
                            return result
            except _BreakRepeat:
                # ``break: loop`` and legacy ``break: repeat`` both exit the
                # nearest loop-like block.
                self._append_trace_row(
                    trace_path, step, "ok", iterations=iter_total
                )
                return None
            self._append_trace_row(trace_path, step, "ok", iterations=iter_total)
            return None
        if "while_match" in step:
            reg = str(step.get("while_match") or "").strip()
            try:
                max_iters = int(step.get("max", 20))
            except (TypeError, ValueError):
                max_iters = 20
            max_iters = max(0, max_iters)
            inner_steps = step.get("steps")
            if not isinstance(inner_steps, list) or not inner_steps:
                inner_steps = [{"click": reg}]

            iterations = 0
            for iter_idx in range(max_iters):
                row = await self._match_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    scenario_key=scenario_key,
                    step=step,
                    region=reg,
                )
                if row is None or not bool(row.get("matched")):
                    break
                iter_path = (
                    f"{trace_path}.{iter_idx}" if trace_path else str(iter_idx)
                )
                self._append_trace_row(
                    iter_path, None, "iter", summary=f"iter {iter_idx}"
                )
                try:
                    for inner_idx, inner in enumerate(inner_steps):
                        if not isinstance(inner, dict):
                            continue
                        result = await self._run_inline_step(
                            inner,
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=scenario_key,
                            trace_path=f"{iter_path}.{inner_idx}",
                        )
                        if result is not None:
                            return result
                except _BreakRepeat:
                    # Propagate to the nearest `repeat:` handler.
                    raise
                iterations += 1

            if iterations:
                logger.info(
                    "dsl_scenario: nested while_match done scenario=%s region=%s iterations=%d",
                    _scen(scenario_key),
                    reg,
                    iterations,
                )
            else:
                logger.debug(
                    "dsl_scenario: nested while_match done scenario=%s region=%s iterations=%d",
                    _scen(scenario_key),
                    reg,
                    iterations,
                )
            self._append_trace_row(trace_path, step, "ok", iterations=iterations)
            return None
        if "swipe_direction" in step:
            spec = step.get("swipe_direction")
            if isinstance(spec, dict):
                direction = str(spec.get("direction") or "").strip().lower()
                try:
                    delta = int(spec.get("delta") or 0)
                except (TypeError, ValueError):
                    delta = 0
                try:
                    duration_ms = int(spec.get("duration_ms") or 300)
                except (TypeError, ValueError):
                    duration_ms = 300
            else:
                direction = str(spec or "").strip().lower()
                delta = 350
                duration_ms = 300
            if direction and delta > 0:
                ok = await asyncio.to_thread(
                    actions.swipe_direction,
                    instance_id,
                    direction=direction,
                    delta=delta,
                    duration_ms=duration_ms,
                )
                if not ok:
                    logger.info(
                        "dsl_scenario: swipe blocked — aborting scenario %s",
                        _scen(scenario_key),
                    )
                    await self._clear_step_context(instance_id)
                    self._append_trace_row(
                        trace_path, step, "stopped", reason="swipe_not_approved"
                    )
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata={"scenario": scenario_key, "reason": "swipe_not_approved"},
                    )
                await asyncio.sleep(0.4)
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "wait" in step:
            seconds = _parse_wait_seconds(step.get("wait"))
            if seconds > 0:
                # Chunked sleep so "Run scenario now" can preempt a long ``wait``
                # without sitting through it. Without this, a multi-second wait
                # delays cancellation by exactly its duration.
                chunk = 0.25
                remaining = seconds
                while remaining > 0:
                    step_s = min(chunk, remaining)
                    await asyncio.sleep(step_s)
                    remaining -= step_s
                    if remaining <= 0:
                        break
                    ip = await self._inline_preempt_if_needed(
                        instance_id, scenario_key
                    )
                    if ip is not None:
                        md = dict(ip.metadata or {})
                        self._append_trace_row(
                            trace_path,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or ""),
                        )
                        return ip
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "push_scenario" in step:
            spec = step.get("push_scenario")
            await self._write_step_context(instance_id, scenario=scenario_key)
            if isinstance(spec, dict):
                name = str(spec.get("name") or "").strip()
                try:
                    pr = int(spec.get("priority") or self.priority)
                except (TypeError, ValueError):
                    pr = self.priority
                try:
                    delay_s = float(spec.get("delay_seconds") or 0.0)
                except (TypeError, ValueError):
                    delay_s = 0.0
                skip_dup = bool(spec.get("skip_if_duplicate", True))
            else:
                name = str(spec or "").strip()
                pr = self.priority
                delay_s = 0.0
                skip_dup = True
            if name:
                await _enqueue_scenario(
                    redis_async=self.redis_client,
                    instance_id=instance_id,
                    player_id=self.player_id,
                    scenario=name,
                    priority=pr,
                    run_at=time.time() + max(0.0, delay_s),
                    skip_if_duplicate=skip_dup,
                )
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "exec" in step:
            name = str(step.get("exec") or "").strip()
            if name:
                await self._run_exec_step(name, instance_id)
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "ocr" in step:
            region = str(step.get("ocr") or "").strip()
            if region:
                await self._ocr_region(
                    actions=actions,
                    area_doc=area_doc,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=scenario_key,
                    step=step,
                    region=region,
                )
            self._append_trace_row(trace_path, step, "ok")
            return None
        if "match" in step:
            region = str(step.get("match") or "").strip()
            if region:
                await self._match_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    scenario_key=scenario_key,
                    step=step,
                    region=region,
                )
            self._append_trace_row(trace_path, step, "ok")
            return None
        logger.warning("dsl_scenario: unsupported nested step: %s", step)
        self._append_trace_row(trace_path, step, "skipped", reason="unsupported")
        return None
