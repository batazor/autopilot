from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2

from actions.tap import BotActions, _redis, _require_approval, click_approval_enabled
from analysis.overlay import evaluate_overlay_rules_async
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.template_match import (
    patch_bgr_from_bbox_percent,
    validate_live_bbox_patch_vs_reference_dims,
)
from layout.types import Point, Region
from tasks.base import TaskResult
from tasks.dsl_match_mixin import DslMatchMixin
from tasks.dsl_ocr_mixin import DslOcrMixin
from tasks.dsl_persist_mixin import DslPersistMixin
from tasks.dsl_scenario_helpers import (
    _BreakRepeat,
    _COLOR_WORD_ALIASES,
    _COND_SCREEN_RE,
    _COND_TEXT_RE,
    _DSL_STEP_ACTION_KEYS,
    _decode_redis_value,
    _dsl_cond_allows_step,
    _dsl_step_summary,
    _enqueue_scenario,
    _eval_instance_text_cond,
    _eval_simple_screen_cond,
    _load_area_json,
    _load_yaml,
    _load_yaml_cached,
    _parse_wait_seconds,
    _read_active_player,
    _read_current_screen,
    _read_instance_state_field,
    _repo_root,
    _step_red_dot_requirement,
    _step_tab_active_requirement,
    _strip_quotes,
)
from ui.notifications import push_ui_notification
from ui.redis_client import dsl_preempt_gen_key

logger = logging.getLogger(__name__)

# Cooperative preemption knobs (ADR 0001 §5). Margin is large enough that two
# tasks within the same band don't ping-pong; immunity threshold caps the worst
# case so a high-priority chain can't starve a long-running scenario forever.
PREEMPT_MARGIN = 5_000
PREEMPT_MAX_YIELDS = 3
PREEMPT_YIELD_COUNT_TTL_SECONDS = 300


def _yield_count_key(instance_id: str, task_id: str) -> str:
    return f"wos:instance:{instance_id}:yield_count:{task_id}"


@dataclass
class DslScenarioTask(DslPersistMixin, DslMatchMixin, DslOcrMixin):
    """Generic runner for imperative DSL scenario YAML.

    This is the bridge that lets us keep scenario logic in YAML, while the worker still executes
    tasks from the Redis queue.
    """

    task_id: str
    player_id: str
    priority: int = 80_000
    cooldown_seconds: int = 1
    is_cooperative: bool = False
    skip_account_check: bool = field(default=True, init=False)
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="dsl_scenario", init=False)

    scenario_key: str = ""
    tap_region: str = ""
    tap_x_pct: float | None = None
    tap_y_pct: float | None = None
    start_step_index: int = 0
    # Rank-time effective_priority from RedisQueue.pop_due. Drives cooperative
    # preemption (ADR 0001 §5): a pending task wins only if it outranks us by
    # PREEMPT_MARGIN. Defaults to ``priority`` when unset (legacy callers).
    effective_priority: int = 0
    # Last `match:` result (best-effort), used to tap at the actual matched location
    # instead of the static region center when `*_search` is involved.
    _last_match_region: str = field(default="", init=False, repr=False)
    _last_match_row: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _last_tap_region_clicked: str = field(default="", init=False, repr=False)
    # Set when ``_tap_region`` ran an implicit auto-match (no explicit ``match:`` in
    # the scenario). Lets ``_point_for_region_action`` use the engine-reported best
    # position even if the score didn't clear ``threshold`` — semantically the user
    # said "this button is here, find it" rather than "verify it's there".
    _implicit_match_for_region: str = field(default="", init=False, repr=False)
    _ocr_client: Any | None = field(default=None, init=False, repr=False)
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]] = field(
        default_factory=dict, init=False, repr=False
    )
    # Snapshot of ``dsl_preempt_gen`` at scenario start; debug UI bumps the counter to exit early.
    _preempt_gen_at_start: int = field(default=0, init=False, repr=False)

    async def _read_dsl_preempt_gen(self, instance_id: str) -> int:
        if self.redis_client is None:
            return 0
        try:
            raw = await self.redis_client.get(dsl_preempt_gen_key(instance_id))
            if raw is None:
                return 0
            s = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            return int(s)
        except Exception:
            return 0

    async def _preempted_by_new_debug(self, instance_id: str) -> bool:
        if self.redis_client is None:
            return False
        try:
            cur = await self._read_dsl_preempt_gen(instance_id)
            return cur > int(self._preempt_gen_at_start)
        except Exception:
            return False

    async def _read_yield_count(self, instance_id: str) -> int:
        if self.redis_client is None or not self.task_id:
            return 0
        try:
            raw = await self.redis_client.get(_yield_count_key(instance_id, self.task_id))
            if raw is None:
                return 0
            s = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            return int(s)
        except Exception:
            return 0

    async def _bump_yield_count(self, instance_id: str) -> int:
        if self.redis_client is None or not self.task_id:
            return 0
        key = _yield_count_key(instance_id, self.task_id)
        try:
            new = await self.redis_client.incr(key)
            with suppress(Exception):
                await self.redis_client.expire(key, PREEMPT_YIELD_COUNT_TTL_SECONDS)
            return int(new)
        except Exception:
            return 0

    async def _preempted_by_higher_priority(
        self, instance_id: str, step_index: int
    ) -> TaskResult | None:
        """Yield this scenario if a pending task outranks us by ``PREEMPT_MARGIN``.

        Anti-starvation: after ``PREEMPT_MAX_YIELDS`` yields for this ``task_id``
        within ``PREEMPT_YIELD_COUNT_TTL_SECONDS``, we become immune until the
        TTL drops the counter.
        """
        if self.redis_client is None:
            return None
        my_eff = int(self.effective_priority) or int(self.priority)
        yc = await self._read_yield_count(instance_id)
        immune = yc >= PREEMPT_MAX_YIELDS

        try:
            from scheduler.queue import RedisQueue
            q = RedisQueue(self.redis_client)
            cs = await _read_current_screen(instance_id, self.redis_client) or ""
            top = await q.peek_top_due(instance_id, current_screen=cs)
        except Exception:
            logger.debug("preempt peek failed", exc_info=True)
            return None
        if top is None:
            return None
        if top.task_id == self.task_id:
            return None
        top_eff = int(top.effective_priority) or int(top.priority)
        gap = top_eff - my_eff
        if gap < PREEMPT_MARGIN:
            return None

        if immune:
            logger.info(
                "dsl_scenario preempt: immune at step=%s (yield_count=%s) — "
                "running=%s eff=%s, top=%s eff=%s gap=%s",
                step_index,
                yc,
                self.scenario_key,
                my_eff,
                top.task_type,
                top_eff,
                gap,
            )
            return None

        new_yc = await self._bump_yield_count(instance_id)
        logger.info(
            "dsl_scenario preempt: yielding at step=%s yield_count=%s — "
            "%s eff=%s preempted_by=%s eff=%s gap=%s",
            step_index,
            new_yc,
            self.scenario_key,
            my_eff,
            top.task_type,
            top_eff,
            gap,
        )
        return TaskResult(
            success=False,
            next_run_at=datetime.now(),
            metadata={
                "scenario": self.scenario_key,
                "reason": "preempted_by_higher_priority",
                "preempted": True,
                "preempted_by": top.task_type,
                "preempted_by_priority": top_eff,
                "running_effective_priority": my_eff,
                "yielded_at_step": step_index,
                "yield_count": new_yc,
            },
        )

    async def _inline_preempt_if_needed(
        self, instance_id: str, scenario_key: str
    ) -> TaskResult | None:
        if not await self._preempted_by_new_debug(instance_id):
            return None
        await self._clear_step_context(instance_id)
        logger.info(
            "dsl_scenario: preempted by debug Run scenario now — %s",
            _scen(scenario_key),
        )
        return TaskResult(
            success=False,
            next_run_at=None,
            metadata={
                "scenario": scenario_key,
                "reason": "dsl_preempted_debug",
                "preempted": True,
            },
        )


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

        pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat()) if region else None
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

        repo_root = Path(__file__).resolve().parent.parent
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
        pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat()) if region else None
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
    ) -> TaskResult | None:
        if "break" in step:
            tgt = str(step.get("break") or "").strip().lower()
            if tgt in {"loop", "repeat"}:
                raise _BreakRepeat()
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

            pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat()) if region else None
            if pair is None:
                logger.warning("dsl_scenario: unknown region %r for long_click", region)
                return None
            _entry, reg = pair
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                logger.warning("dsl_scenario: missing bbox for long_click region %r", region)
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
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata={"scenario": scenario_key, "reason": "long_click_not_approved"},
                )
            self._last_tap_region_clicked = region
            await asyncio.sleep(0.4)
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
                    return result
                await asyncio.sleep(0.4)
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

            for _ in range(max_iters):
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
                            return None
                try:
                    for inner in inner_steps:
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
                        )
                        if result is not None:
                            return result
                        if self._last_tap_region_clicked:
                            if stop_click_any or (
                                stop_click_regs
                                and self._last_tap_region_clicked in stop_click_regs
                            ):
                                return None
                except _BreakRepeat:
                    return None
            return None
        if "loop" in step:
            spec = step.get("loop")
            if not isinstance(spec, dict):
                return None
            inner_steps = spec.get("steps")
            if not isinstance(inner_steps, list) or not inner_steps:
                return None

            cond_expr_raw = spec.get("cond")
            cond_expr = (
                str(cond_expr_raw).strip()
                if cond_expr_raw is not None and not isinstance(cond_expr_raw, bool)
                else None
            ) or None
            until_cond_raw = spec.get("until_cond")
            until_cond = (
                str(until_cond_raw).strip()
                if until_cond_raw is not None and not isinstance(until_cond_raw, bool)
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

            try:
                for _ in range(max_iters):
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    # ``cond`` continues while True; ``until_cond`` continues
                    # while False — both are re-evaluated each iteration so
                    # inner OCR / exec steps can flip state and exit the loop.
                    if cond_expr is not None and not await _dsl_cond_allows_step(
                        {"cond": cond_expr},
                        instance_id,
                        self.redis_client,
                        state_flat=self._state_flat(),
                    ):
                        break
                    if until_cond is not None and await _dsl_cond_allows_step(
                        {"cond": until_cond},
                        instance_id,
                        self.redis_client,
                        state_flat=self._state_flat(),
                    ):
                        break

                    for inner in inner_steps:
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
                        )
                        if result is not None:
                            return result
            except _BreakRepeat:
                # ``break: loop`` and legacy ``break: repeat`` both exit the
                # nearest loop-like block.
                return None
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
            for _ in range(max_iters):
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
                try:
                    for inner in inner_steps:
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
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata={"scenario": scenario_key, "reason": "swipe_not_approved"},
                    )
                await asyncio.sleep(0.4)
            return None
        if "wait" in step:
            seconds = _parse_wait_seconds(step.get("wait"))
            if seconds > 0:
                await asyncio.sleep(seconds)
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
            return None
        if "exec" in step:
            name = str(step.get("exec") or "").strip()
            if name:
                await self._run_exec_step(name, instance_id)
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
            return None
        logger.warning("dsl_scenario: unsupported nested step: %s", step)
        return None

    async def execute(self, instance_id: str) -> TaskResult:
        key = str(self.scenario_key or "").strip()
        if not key:
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "missing_scenario_key"},
            )

        # Wipe the previous scenario's `dsl_last_*` audit snapshot so the
        # click-approvals UI doesn't show stale guard outcomes during the
        # window between scenario start and our first match/ocr/color step.
        await self._reset_dsl_audit_state(instance_id)

        repo_root = _repo_root()

        # Resolve scenario by key: search recursively under `scenarios/`, excluding drafts.
        scenarios_root = repo_root / "scenarios"
        if not scenarios_root.is_dir():
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "scenario_root_missing", "path": str(scenarios_root)},
            )

        hits: list[Path] = []
        for p in scenarios_root.rglob(f"{key}.yaml"):
            rel = p.relative_to(scenarios_root).as_posix()
            # Exclude drafts (never execute).
            if rel.startswith("drafts/"):
                continue
            hits.append(p)

        if not hits:
            await push_ui_notification(
                self.redis_client,
                instance_id,
                kind="dsl.scenario_not_found",
                message=f"Scenario not found: {key}",
                level="error",
                event_id=f"dsl:scenario_not_found:{instance_id}:{key}",
                payload={"scenario": key},
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "scenario_not_found", "key": key},
            )
        # Deterministic: prefer shorter relative path, then lexicographic.
        hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
        path = hits[0]

        doc = _load_yaml(path)
        steps = doc.get("steps")
        if not isinstance(steps, list):
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "invalid_steps", "path": str(path)},
            )
        steps_total_n = len(steps)
        steps_trace: list[dict[str, Any]] = []

        def _trace_row(i: int, step_obj: Any, status: str, **kw: Any) -> None:
            summ = _dsl_step_summary(step_obj) if isinstance(step_obj, dict) else "(non-dict)"
            row: dict[str, Any] = {"i": i, "summary": summ, "status": status}
            for k, v in kw.items():
                if v is not None:
                    row[k] = v
            steps_trace.append(row)

        def _fin(meta: dict[str, Any], *, completed: bool) -> dict[str, Any]:
            m = dict(meta)
            m["steps_trace"] = list(steps_trace)
            m["steps_total"] = steps_total_n
            m["scenario_completed"] = completed
            if self.start_step_index:
                m["resume_from_step_index"] = int(self.start_step_index)
            return m

        self._preempt_gen_at_start = await self._read_dsl_preempt_gen(instance_id)

        raw_root_cond = doc.get("cond")
        if raw_root_cond is not None and not isinstance(raw_root_cond, bool):
            cond_s = str(raw_root_cond).strip()
            if cond_s:
                if not await _dsl_cond_allows_step(
                    {"cond": raw_root_cond},
                    instance_id,
                    self.redis_client,
                    state_flat=self._state_flat(),
                ):
                    await self._clear_step_context(instance_id)
                    logger.debug(
                        "dsl_scenario: scenario skipped by root cond (%s)", cond_s
                    )
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "scenario_cond_false",
                                "cond": cond_s,
                            },
                            completed=True,
                        ),
                    )

        if await self._preempted_by_new_debug(instance_id):
            await self._clear_step_context(instance_id)
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {
                        "scenario": key,
                        "reason": "dsl_preempted_debug",
                        "preempted": True,
                    },
                    completed=False,
                ),
            )

        actions = BotActions()
        area_doc = _load_area_json(repo_root)
        dev_w, dev_h = actions.screen_resolution(instance_id)

        if await self._preempted_by_new_debug(instance_id):
            await self._clear_step_context(instance_id)
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {
                        "scenario": key,
                        "reason": "dsl_preempted_debug",
                        "preempted": True,
                    },
                    completed=False,
                ),
            )

        # Optional root-level `node: <screen>` — navigate the FSM to the target
        # screen before running steps. Lets DSL scenarios skip explicit
        # `click: <btn>` chains when destination is already in screen_graph.
        target_node = str(doc.get("node") or "").strip()
        # `device_level: true` opts a scenario out of identity gating (see
        # `RedisQueue.pop_due`).  Reused here as the default mode for `while_match`:
        # device-level scenarios (popup dismissals, identity probes) keep the
        # legacy "0 iterations = success" semantics, since their triggers may
        # legitimately have already been resolved.  Player-bound scenarios get
        # initial-probe retries + strict zero-iteration failure so the work
        # actually happens (or is properly retried).
        is_device_level = doc.get("device_level") is True
        if target_node and self.start_step_index <= 0:
            nav_ok = await self._navigate_to_node(
                instance_id,
                target_node,
                actions=actions,
                scenario_key=key,
            )
            if nav_ok and self.redis_client is not None:
                with suppress(Exception):
                    await self.redis_client.hset(
                        f"wos:instance:{instance_id}:state", "nav_error", ""
                    )
            if not nav_ok:
                await self._clear_step_context(instance_id)
                if self.redis_client is not None:
                    with suppress(Exception):
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            mapping={
                                "nav_error": f"navigation_failed: {key} → {target_node} (no route or verify failed)",
                                "current_screen": "",
                            },
                        )
                    with suppress(Exception):
                        from scheduler.queue import RedisQueue
                        q = RedisQueue(self.redis_client)
                        await q.schedule(
                            task_id=f"nav_fail:where_i_am:{instance_id}:{int(time.time())}",
                            player_id="",
                            task_type="where_i_am",
                            priority=90_000,
                            run_at=time.time(),
                            instance_id=instance_id,
                            skip_if_duplicate=True,
                        )
                return TaskResult(
                    success=False,
                    next_run_at=datetime.now() + timedelta(minutes=5),
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "navigation_failed",
                            "target_node": target_node,
                        },
                        completed=False,
                    ),
                )

        step_index = self.start_step_index
        require_identity_resolution = key == "who_i_am" and not str(self.player_id or "").strip()
        while step_index < len(steps):
            step = steps[step_index]
            _resumable_step = step_index  # capture before increment for resume tracking
            step_index += 1
            if await self._preempted_by_new_debug(instance_id):
                await self._clear_step_context(instance_id)
                _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                return TaskResult(
                    success=False,
                    next_run_at=None,
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "dsl_preempted_debug",
                            "preempted": True,
                        },
                        completed=False,
                    ),
                )
            preempt_yield = await self._preempted_by_higher_priority(
                instance_id, _resumable_step
            )
            if preempt_yield is not None:
                await self._clear_step_context(instance_id)
                _trace_row(
                    _resumable_step,
                    step,
                    "preempted",
                    reason="preempted_by_higher_priority",
                )
                md = dict(preempt_yield.metadata or {})
                md["resume_from_step_index"] = (
                    0 if target_node else int(_resumable_step)
                )
                return TaskResult(
                    success=preempt_yield.success,
                    next_run_at=preempt_yield.next_run_at,
                    metadata=_fin(md, completed=False),
                )
            # Persist current step so hand-pointer resume knows where to continue.
            if self.redis_client is not None:
                with suppress(Exception):
                    await self.redis_client.hset(
                        f"wos:instance:{instance_id}:state",
                        "last_active_scenario_step",
                        str(_resumable_step),
                    )
            if not isinstance(step, dict):
                _trace_row(_resumable_step, step, "skipped_invalid")
                continue
            if not await _dsl_cond_allows_step(
                step,
                instance_id,
                self.redis_client,
                state_flat=self._state_flat(),
            ):
                logger.debug("dsl_scenario: step skipped by cond (%s)", step.get("cond"))
                _trace_row(_resumable_step, step, "skipped_cond")
                continue
            grouped = step.get("steps")
            if (
                isinstance(grouped, list)
                and grouped
                and not _DSL_STEP_ACTION_KEYS.intersection(step.keys())
            ):
                await self._write_step_context(instance_id, scenario=key)
                for inner in grouped:
                    if not isinstance(inner, dict):
                        continue
                    if not await _dsl_cond_allows_step(
                        inner,
                        instance_id,
                        self.redis_client,
                        state_flat=self._state_flat(),
                    ):
                        logger.debug(
                            "dsl_scenario: grouped step skipped by cond (%s)",
                            inner.get("cond"),
                        )
                        continue
                    result = await self._run_inline_step(
                        inner,
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        dev_w=dev_w,
                        dev_h=dev_h,
                        scenario_key=key,
                    )
                    if result is not None:
                        md = dict(result.metadata or {})
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or ""),
                        )
                        return TaskResult(
                            success=result.success,
                            next_run_at=result.next_run_at,
                            metadata=_fin(md, completed=False),
                        )
                _trace_row(_resumable_step, step, "ok")
                continue
            if "match" in step:
                reg = str(step.get("match") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                row = await self._match_region(
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    scenario_key=key,
                    step=step,
                    region=reg,
                )
                if row is None:
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "early_exit", reason="match_region_not_found")
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "match_region_not_found",
                                "region": reg,
                            },
                            completed=False,
                        ),
                    )
                matched = bool(row.get("matched"))
                if not matched:
                    logger.info(
                        "dsl_scenario: match guard failed — skipping scenario %s region=%s row=%s",
                        _scen(key),
                        reg,
                        row,
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "early_exit", reason="match_guard_failed")
                    return TaskResult(
                        success=True,
                        next_run_at=None,
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "match_guard_failed",
                                "region": reg,
                                "match": row if isinstance(row, dict) else None,
                            },
                            completed=False,
                        ),
                    )
                _trace_row(_resumable_step, step, "ok")
                continue
            if "while_match" in step:
                reg = str(step.get("while_match") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                try:
                    max_iters = int(step.get("max", 20))
                except (TypeError, ValueError):
                    max_iters = 20
                max_iters = max(0, max_iters)
                inner_steps = step.get("steps")
                if not isinstance(inner_steps, list) or not inner_steps:
                    inner_steps = [{"click": reg}]

                # Player-bound scenarios retry the *initial* probe to absorb
                # screen-settling lag after navigation.  Subsequent probes are
                # single-shot — once we've matched once, lack of a match means
                # the work is done.  Device-level scenarios keep legacy 1-shot
                # semantics so popup dismissals don't pause for nothing.
                #
                # YAML form:
                #   retry:
                #     attempts: 5
                #     interval: 500ms     # also accepts "0.5s" or raw seconds
                default_attempts = 1 if is_device_level else 5
                default_interval_s = 0.5
                default_strict = not is_device_level
                retry_cfg = step.get("retry")
                if not isinstance(retry_cfg, dict):
                    retry_cfg = {}
                try:
                    initial_attempts = int(retry_cfg.get("attempts", default_attempts))
                except (TypeError, ValueError):
                    initial_attempts = default_attempts
                initial_attempts = max(1, initial_attempts)
                if "interval" in retry_cfg:
                    attempt_interval_s = _parse_wait_seconds(retry_cfg.get("interval"))
                else:
                    attempt_interval_s = default_interval_s
                attempt_interval_s = max(0.0, attempt_interval_s)
                strict = bool(step.get("strict", default_strict))

                iterations = 0
                inner_result: TaskResult | None = None
                for _ in range(max_iters):
                    if await self._preempted_by_new_debug(instance_id):
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "dsl_preempted_debug",
                                    "preempted": True,
                                },
                                completed=False,
                            ),
                        )
                    probe_attempts = initial_attempts if iterations == 0 else 1
                    matched = False
                    for attempt in range(probe_attempts):
                        row = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=key,
                            step=step,
                            region=reg,
                        )
                        if row is not None and bool(row.get("matched")):
                            matched = True
                            break
                        if attempt < probe_attempts - 1:
                            await asyncio.sleep(attempt_interval_s)
                    if not matched:
                        break
                    for inner in inner_steps:
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
                            scenario_key=key,
                        )
                        if result is not None:
                            inner_result = result
                            break
                    if inner_result is not None:
                        break
                    iterations += 1

                if inner_result is not None:
                    md = dict(inner_result.metadata or {})
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return TaskResult(
                        success=inner_result.success,
                        next_run_at=inner_result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )

                if iterations == 0 and strict:
                    # Strict mode: zero iterations after initial-probe retries
                    # means the work didn't happen.  Reschedule so the next
                    # `pop_due` cycle gets another shot instead of yielding to
                    # whatever lower-priority task is in the queue.
                    approved = await self._pause_for_while_match_no_iterations_approval(
                        actions=actions,
                        instance_id=instance_id,
                        scenario_key=key,
                        region=reg,
                        attempts=initial_attempts,
                        interval_s=attempt_interval_s,
                    )
                    if not approved:
                        await self._clear_step_context(instance_id)
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason="while_match_no_iterations_not_approved",
                        )
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "while_match_no_iterations_not_approved",
                                    "region": reg,
                                },
                                completed=False,
                            ),
                        )
                    logger.info(
                        "dsl_scenario: while_match no_iterations scenario=%s region=%s "
                        "attempts=%d → soft-fail with retry",
                        _scen(key),
                        reg,
                        initial_attempts,
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(
                        _resumable_step,
                        step,
                        "early_exit",
                        reason="while_match_no_iterations",
                    )
                    return TaskResult(
                        success=False,
                        next_run_at=datetime.now() + timedelta(seconds=30),
                        metadata=_fin(
                            {
                                "scenario": key,
                                "reason": "while_match_no_iterations",
                                "region": reg,
                                "attempts": initial_attempts,
                                "interval": attempt_interval_s,
                            },
                            completed=False,
                        ),
                    )

                logger.info(
                    "dsl_scenario: while_match done scenario=%s region=%s iterations=%d",
                    _scen(key),
                    reg,
                    iterations,
                )
                _trace_row(_resumable_step, step, "ok")
                continue
            if "repeat" in step:
                await self._write_step_context(instance_id, scenario=key)
                spec = step.get("repeat")
                if isinstance(spec, dict):
                    try:
                        max_iters = int(spec.get("max", 1))
                    except (TypeError, ValueError):
                        max_iters = 1
                    inner_steps = spec.get("steps")
                    until_match = str(spec.get("until_match") or "").strip()
                    until_any = spec.get("until_any_match")
                else:
                    try:
                        max_iters = int(spec or 1)
                    except (TypeError, ValueError):
                        max_iters = 1
                    inner_steps = step.get("steps")
                    until_match = ""
                    until_any = None

                max_iters = max(0, max_iters)
                if not isinstance(inner_steps, list) or not inner_steps:
                    _trace_row(_resumable_step, step, "skipped_empty")
                    continue

                until_any_list: list[str] = []
                if isinstance(until_any, list):
                    until_any_list = [
                        str(x or "").strip()
                        for x in until_any
                        if str(x or "").strip()
                    ]

                for _ in range(max_iters):
                    if await self._preempted_by_new_debug(instance_id):
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "preempted", reason="dsl_preempted_debug")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "dsl_preempted_debug",
                                    "preempted": True,
                                },
                                completed=False,
                            ),
                        )
                    if until_match:
                        row = await self._match_region(
                            actions=actions,
                            area_doc=area_doc,
                            repo_root=repo_root,
                            instance_id=instance_id,
                            scenario_key=key,
                            step=step,
                            region=until_match,
                        )
                        if row is not None and bool(row.get("matched")):
                            break
                    if until_any_list:
                        any_hit = False
                        for reg in until_any_list:
                            row2 = await self._match_region(
                                actions=actions,
                                area_doc=area_doc,
                                repo_root=repo_root,
                                instance_id=instance_id,
                                scenario_key=key,
                                step=step,
                                region=reg,
                            )
                            if row2 is not None and bool(row2.get("matched")):
                                any_hit = True
                                break
                        if any_hit:
                            break
                    try:
                        for inner in inner_steps:
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
                                scenario_key=key,
                            )
                            if result is not None:
                                md = dict(result.metadata or {})
                                _trace_row(
                                    _resumable_step,
                                    step,
                                    "stopped",
                                    reason=str(md.get("reason") or ""),
                                )
                                return TaskResult(
                                    success=result.success,
                                    next_run_at=result.next_run_at,
                                    metadata=_fin(md, completed=False),
                                )
                    except _BreakRepeat:
                        # Stop the nearest loop-like block and continue with the next outer step.
                        break
                _trace_row(_resumable_step, step, "ok")
                continue
            if "loop" in step:
                # Delegate to the inline implementation: loop guards (`cond` /
                # `until_cond` / `ttl`) are re-evaluated each iteration there
                # and inner steps go through the same `_run_inline_step` path
                # that the rest of the DSL uses.
                await self._write_step_context(instance_id, scenario=key)
                result = await self._run_inline_step(
                    step,
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=key,
                )
                if result is not None:
                    md = dict(result.metadata or {})
                    _trace_row(
                        _resumable_step,
                        step,
                        "stopped",
                        reason=str(md.get("reason") or ""),
                    )
                    return TaskResult(
                        success=result.success,
                        next_run_at=result.next_run_at,
                        metadata=_fin(md, completed=False),
                    )
                _trace_row(_resumable_step, step, "ok")
                continue
            if "push_scenario" in step:
                await self._write_step_context(instance_id, scenario=key)
                spec = step.get("push_scenario")
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
                _trace_row(_resumable_step, step, "ok")
                continue
            if "swipe_direction" in step:
                await self._write_step_context(instance_id, scenario=key)
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
                            "dsl_scenario: swipe blocked — aborting scenario %s", _scen(key)
                        )
                        await self._clear_step_context(instance_id)
                        _trace_row(_resumable_step, step, "stopped", reason="swipe_not_approved")
                        return TaskResult(
                            success=False,
                            next_run_at=None,
                            metadata=_fin(
                                {"scenario": key, "reason": "swipe_not_approved"},
                                completed=False,
                            ),
                        )
                    await asyncio.sleep(0.4)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "ocr" in step:
                reg = str(step.get("ocr") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if reg:
                    ocr_steps = [step]
                    while step_index < len(steps):
                        next_step = steps[step_index]
                        if not isinstance(next_step, dict) or "ocr" not in next_step:
                            break
                        step_index += 1
                        if not await _dsl_cond_allows_step(
                            next_step,
                            instance_id,
                            self.redis_client,
                            state_flat=self._state_flat(),
                        ):
                            logger.debug(
                                "dsl_scenario: step skipped by cond (%s)",
                                next_step.get("cond"),
                            )
                            continue
                        if str(next_step.get("ocr") or "").strip():
                            ocr_steps.append(next_step)
                    if len(ocr_steps) > 1:
                        await self._ocr_region_bulk(
                            actions=actions,
                            area_doc=area_doc,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            steps=ocr_steps,
                        )
                    else:
                        await self._ocr_region(
                            actions=actions,
                            area_doc=area_doc,
                            instance_id=instance_id,
                            dev_w=dev_w,
                            dev_h=dev_h,
                            scenario_key=key,
                            step=step,
                            region=reg,
                        )
                    active_player = await _read_active_player(instance_id, self.redis_client)
                    if require_identity_resolution and reg == "player_id" and not active_player:
                        logger.info(
                            "dsl_scenario: identity OCR did not set active_player "
                            "scenario=%s region=%s — retry",
                            _scen(key),
                            reg,
                        )
                        await self._clear_step_context(instance_id)
                        _trace_row(
                            _resumable_step,
                            step,
                            "early_exit",
                            reason="identity_not_resolved",
                        )
                        return TaskResult(
                            success=False,
                            next_run_at=datetime.now() + timedelta(seconds=30),
                            metadata=_fin(
                                {
                                    "scenario": key,
                                    "reason": "identity_not_resolved",
                                    "region": reg,
                                },
                                completed=False,
                            ),
                        )
                _trace_row(_resumable_step, step, "ok")
                continue
            if "exec" in step:
                cmd = str(step.get("exec") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if cmd:
                    await self._run_exec_step(cmd, instance_id)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "set_node" in step:
                node = str(step.get("set_node") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not node:
                    _trace_row(_resumable_step, step, "skipped_empty")
                    continue
                approval_payload: dict[str, object] = {
                    "type": "set_node",
                    "set_node": node,
                    "source": {
                        "component": "tasks.dsl_scenario.DslScenarioTask",
                        "note": "DSL set_node step (approval mode)",
                    },
                }
                attach_preview = getattr(actions, "attach_approval_preview", None)
                if callable(attach_preview):
                    with suppress(Exception):
                        await asyncio.to_thread(attach_preview, instance_id, approval_payload)
                ok, req_id = await asyncio.to_thread(
                    _require_approval,
                    instance_id,
                    approval_payload,
                )
                if not ok:
                    logger.info(
                        "dsl_scenario: set_node rejected or blocked — aborting scenario %s",
                        _scen(key),
                    )
                    await self._clear_step_context(instance_id)
                    _trace_row(_resumable_step, step, "stopped", reason="set_node_not_approved")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "set_node_not_approved"},
                            completed=False,
                        ),
                    )
                if self.redis_client is not None:
                    with suppress(Exception):
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            "current_screen",
                            node,
                        )
                if req_id is not None:
                    try:
                        _redis().delete(f"wos:ui:click_approval:current:{instance_id}")
                        _redis().delete(f"wos:ui:click_approval:response:{req_id}")
                    except Exception:
                        logger.debug("approval cleanup after set_node failed", exc_info=True)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "click" in step:
                reg = str(step.get("click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                pair = screen_region_by_name(area_doc, reg, state_flat=self._state_flat()) if reg else None
                # For click approvals: expose region + optional threshold (overlay queue may
                # already set ``current_task_threshold``; do not overwrite).
                if reg and self.redis_client is not None:
                    with suppress(Exception):
                        st_key = f"wos:instance:{instance_id}:state"
                        mapping: dict[str, str] = {"current_task_region": reg}
                        if pair is not None:
                            raw_thr = pair[1].get("threshold")
                            thr_txt = ""
                            if isinstance(raw_thr, (int, float)):
                                thr_txt = f"{float(raw_thr):.6g}"
                            elif isinstance(raw_thr, str) and str(raw_thr).strip():
                                thr_txt = str(raw_thr).strip()
                            if thr_txt:
                                prev = await self.redis_client.hget(
                                    st_key, "current_task_threshold"
                                )
                                prev_s = (
                                    prev.decode()
                                    if isinstance(prev, bytes)
                                    else str(prev or "")
                                ).strip()
                                if not prev_s:
                                    mapping["current_task_threshold"] = thr_txt
                        await self.redis_client.hset(st_key, mapping=mapping)
                if reg:
                    result = await self._tap_region(
                        actions=actions,
                        area_doc=area_doc,
                        repo_root=repo_root,
                        instance_id=instance_id,
                        dev_w=dev_w,
                        dev_h=dev_h,
                        scenario_key=key,
                        region=reg,
                        step=step,
                    )
                    if result is not None:
                        md = dict(result.metadata or {})
                        _trace_row(
                            _resumable_step,
                            step,
                            "stopped",
                            reason=str(md.get("reason") or ""),
                        )
                        return TaskResult(
                            success=result.success,
                            next_run_at=result.next_run_at,
                            metadata=_fin(md, completed=False),
                        )
                    await asyncio.sleep(0.4)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "long_click" in step:
                reg = str(step.get("long_click") or "").strip()
                await self._write_step_context(instance_id, scenario=key)
                if not reg:
                    _trace_row(_resumable_step, step, "ok")
                    continue
                pair = screen_region_by_name(area_doc, reg, state_flat=self._state_flat())
                if pair is None:
                    _trace_row(_resumable_step, step, "stopped", reason="unknown_region")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin({"scenario": key, "reason": "unknown_region"}, completed=False),
                    )
                _entry, reg_doc = pair
                bbox = reg_doc.get("bbox")
                if not isinstance(bbox, dict):
                    _trace_row(_resumable_step, step, "stopped", reason="missing_bbox")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin({"scenario": key, "reason": "missing_bbox"}, completed=False),
                    )
                raw_dur = step.get("duration")
                if raw_dur is None:
                    raw_dur = step.get("wait")
                duration_ms = 800
                with suppress(Exception):
                    dur_s = _parse_wait_seconds(raw_dur)
                    if dur_s > 0:
                        duration_ms = int(round(dur_s * 1000.0))
                pt = self._point_for_region_action(reg, bbox, dev_w, dev_h)
                ok = False
                with suppress(Exception):
                    ok = bool(
                        await asyncio.to_thread(
                            actions.long_tap,
                            instance_id,
                            pt,
                            duration_ms=duration_ms,
                        )
                    )
                if not ok:
                    _trace_row(_resumable_step, step, "stopped", reason="long_click_not_approved")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "long_click_not_approved"},
                            completed=False,
                        ),
                    )
                await asyncio.sleep(0.4)
                _trace_row(_resumable_step, step, "ok")
                continue
            if "wait" in step:
                # Supports "1200ms" (string) or seconds (number).
                w = step.get("wait")
                await self._write_step_context(instance_id, scenario=key)
                seconds = _parse_wait_seconds(w)
                if seconds > 0:
                    await asyncio.sleep(seconds)
                _trace_row(_resumable_step, step, "ok")
                continue
        logger.info("dsl_scenario done: %s (%s)", _scen(key), instance_id)
        await self._clear_step_context(instance_id)
        return TaskResult(
            success=True,
            next_run_at=None,
            metadata=_fin({"scenario": key}, completed=True),
        )
