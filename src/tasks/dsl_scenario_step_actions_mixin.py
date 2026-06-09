"""Action/leaf top-level step handlers for ``DslScenarioExecuteMixin``.

Each ``_exec_*_step`` method is the verbatim body of one ``if "<kind>" in
step:`` branch from the historical monolithic ``execute`` loop. The contract
is shared across all handlers:

- return ``None`` → the step finished; ``execute`` continues with the next
  top-level step (the historical ``continue``).
- return a :class:`TaskResult` → the scenario ends now; ``execute`` returns
  it unchanged (metadata is already ``fr.fin(...)``-wrapped here).

Loop-invariant context (resolved actions, area doc, scenario key, the
``fin`` / ``mark_step_done`` closures…) arrives via
:class:`tasks.dsl_scenario_exec_frame.ExecFrame`. The ``ocr`` handler is the
one mutator: it advances ``fr.step_index`` when it batches a sibling chain of
consecutive ``ocr:`` steps.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import (
    _action_pause_seconds,
    _dsl_cond_allows_step,
    _enqueue_scenario,
    _jittered_wait_seconds,
    _parse_wait_seconds,
    _read_active_player,
    _resolve_push_delay_seconds,
    _trace_exec_result_kwargs,
)

if TYPE_CHECKING:
    from tasks.dsl_scenario_exec_frame import ExecFrame

logger = logging.getLogger(__name__)

# TYPE_CHECKING-only base: gives ty visibility into every host attribute and
# sibling-mixin method without changing the runtime MRO of DslScenarioTask.
# See ``tasks/_dsl_task_host.py`` for the rationale.
if TYPE_CHECKING:
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object


class DslScenarioStepActionsMixin(_Base):
    """Grouped / ``loop`` / ``push_scenario`` / ``ocr`` / ``click`` … handlers."""

    async def _exec_grouped_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        area_doc = fr.area_doc
        repo_root = fr.repo_root
        dev_w, dev_h = fr.dev_w, fr.dev_h
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        grouped = step.get("steps")
        await self._write_step_context(instance_id, scenario=key)
        for inner_idx, raw_inner in enumerate(grouped or []):
            if not isinstance(raw_inner, dict):
                continue
            inner: dict[str, Any] = cast("dict[str, Any]", raw_inner)
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
                trace_path=f"{_resumable_step}.{inner_idx}",
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_loop_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        # Delegate to the inline implementation: loop guards (`cond` /
        # `ttl`) are re-evaluated each iteration there and inner steps
        # go through the same `_run_inline_step` path that the rest of
        # the DSL uses.
        await self._write_step_context(instance_id, scenario=key)
        result = await self._run_inline_step(
            step,
            actions=fr.actions,
            area_doc=fr.area_doc,
            repo_root=fr.repo_root,
            instance_id=instance_id,
            dev_w=fr.dev_w,
            dev_h=fr.dev_h,
            scenario_key=key,
            trace_path=str(_resumable_step),
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_push_scenario_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        await self._write_step_context(instance_id, scenario=key)
        spec = step.get("push_scenario")
        if isinstance(spec, dict):
            name = str(spec.get("name") or "").strip()
            try:
                pr = int(spec.get("priority") or self.priority)
            except (TypeError, ValueError):
                pr = self.priority
            delay_s = await _resolve_push_delay_seconds(
                spec.get("delay"),
                instance_id=instance_id,
                redis_async=self.redis_client,
                player_id=self.player_id,
            )
            skip_dup = bool(spec.get("skip_if_duplicate", True))
        else:
            name = str(spec or "").strip()
            pr = self.priority
            delay_s = 0.0
            skip_dup = True
        if delay_s is None:
            await _mark_top_level_step_done()
            _trace_row(
                _resumable_step, step, "skipped", reason="delay_unresolved"
            )
            return None
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_system_back_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _fin = fr.fin

        await self._write_step_context(instance_id, scenario=key)
        result = await self._run_system_back_step(
            actions=fr.actions,
            instance_id=instance_id,
            scenario_key=key,
            step=step,
            trace_path=str(_resumable_step),
        )
        if result is not None:
            md = dict(result.metadata or {})
            return TaskResult(
                success=result.success,
                next_run_at=result.next_run_at,
                metadata=_fin(md, completed=False),
            )
        return None

    async def _exec_swipe_direction_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

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
            await asyncio.sleep(_action_pause_seconds(0.4))
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_ocr_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        area_doc = fr.area_doc
        dev_w, dev_h = fr.dev_w, fr.dev_h
        steps = fr.steps
        require_identity_resolution = fr.require_identity_resolution
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        reg = str(step.get("ocr") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        if reg:
            ocr_steps = [step]
            while fr.step_index < len(steps):
                next_step = steps[fr.step_index]
                if not isinstance(next_step, dict) or "ocr" not in next_step:
                    break
                fr.step_index += 1
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
            # Check every region in the bulk batch — not just the first.
            # ``reg`` here is the OUTER step's region; a bulk batch like
            # ``[{ocr: a}, {ocr: player.id}, ...]`` would otherwise skip
            # the identity gate because ``reg == "a"``, even though OCR
            # *did* try to resolve identity. Region names follow the
            # ``games/<game>/<id>/area.yaml`` convention (``player.id``
            # with a dot — see ``games/wos/core/who_i_am/area.yaml``).
            identity_regions = {
                str(s.get("ocr") or "").strip() for s in ocr_steps
            }
            if (
                require_identity_resolution
                and "player.id" in identity_regions
                and not active_player
            ):
                logger.info(
                    "dsl_scenario: identity OCR did not set active_player "
                    "scenario=%s regions=%s — retry",
                    _scen(key),
                    sorted(identity_regions),
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
                    next_run_at=datetime.now(tz=UTC) + timedelta(seconds=30),
                    metadata=_fin(
                        {
                            "scenario": key,
                            "reason": "identity_not_resolved",
                            "region": reg,
                        },
                        completed=False,
                    ),
                )
        # ``_ocr_audit_step`` set ``self._last_ocr_row`` on the way out
        # — pass it through so the trace shows confidence/value/text.
        # On bulk OCR this reflects the last region; that's acceptable
        # for a single trace row covering a sibling chain.
        await _mark_top_level_step_done()
        _trace_row(
            _resumable_step, step, "ok", ocr_row=self._last_ocr_row
        )
        return None

    async def _exec_exec_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        cmd = str(step.get("exec") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        exec_row: dict[str, Any] = {}
        if cmd:
            base_args = self.args if isinstance(self.args, dict) else {}
            args = {
                **base_args,
                **{k: v for k, v in step.items() if k not in ("exec", "cond")},
            }
            exec_row = await self._run_exec_step(cmd, instance_id, args)
        await _mark_top_level_step_done()
        exec_row = _trace_exec_result_kwargs(exec_row)
        _trace_row(_resumable_step, step, "ok", **exec_row)
        return None

    async def _exec_click_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        area_doc = fr.area_doc
        repo_root = fr.repo_root
        dev_w, dev_h = fr.dev_w, fr.dev_h
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        reg = str(step.get("click") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        _sf = self._state_flat()
        pair = (
            screen_region_by_name(area_doc, reg, state_flat=_sf) if reg else None
        )
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
                    match_row=self._last_match_row,
                )
                return TaskResult(
                    success=result.success,
                    next_run_at=result.next_run_at,
                    metadata=_fin(md, completed=False),
                )
            await asyncio.sleep(_action_pause_seconds(0.4))
        await _mark_top_level_step_done()
        _trace_row(
            _resumable_step, step, "ok", match_row=self._last_match_row
        )
        return None

    async def _exec_long_click_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        area_doc = fr.area_doc
        dev_w, dev_h = fr.dev_w, fr.dev_h
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        reg = str(step.get("long_click") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        if not reg:
            await _mark_top_level_step_done()
            _trace_row(_resumable_step, step, "ok")
            return None
        pair = screen_region_by_name(area_doc, reg, state_flat=self._state_flat())
        if pair is None:
            _trace_row(_resumable_step, step, "stopped", reason="unknown_region")
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {"scenario": key, "reason": "unknown_region"},
                    completed=False,
                ),
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
        await asyncio.sleep(_action_pause_seconds(0.4))
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_ttl_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _fin = fr.fin
        _trace_row = self._append_trace_row

        # Exit early, reschedule self for ``now + ttl``. Same semantic
        # as the inline handler — mostly used inside ``while_match.else``,
        # but accepted at top level too for the "skip this tick" idiom.
        ttl_s = max(0.0, _parse_wait_seconds(step.get("ttl")))
        await self._clear_step_context(instance_id)
        _trace_row(
            _resumable_step, step, "early_exit", reason="ttl", ttl_s=ttl_s
        )
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=ttl_s),
            metadata=_fin(
                {
                    "scenario": key,
                    "reason": "ttl_exit",
                    "ttl_s": ttl_s,
                },
                completed=False,
            ),
        )

    async def _exec_wait_screen_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        _fin = fr.fin
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        await self._write_step_context(instance_id, scenario=key)
        matched = await self._run_wait_screen_step(
            actions=fr.actions,
            instance_id=instance_id,
            scenario_key=key,
            step=step,
        )
        if not matched:
            await self._clear_step_context(instance_id)
            _trace_row(
                _resumable_step,
                step,
                "stopped",
                reason="wait_screen_timeout",
            )
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata=_fin(
                    {"scenario": key, "reason": "wait_screen_timeout"},
                    completed=False,
                ),
            )
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok", matched=matched)
        return None

    async def _exec_wait_step(
        self, fr: ExecFrame, step: dict[str, Any], _resumable_step: int
    ) -> TaskResult | None:
        key = fr.scenario_key
        instance_id = fr.instance_id
        actions = fr.actions
        _mark_top_level_step_done = fr.mark_step_done
        _trace_row = self._append_trace_row

        # Supports "1200ms" (string) or seconds (number).
        w = step.get("wait")
        await self._write_step_context(instance_id, scenario=key)
        from config.loader import get_settings as _get_settings

        _jitter_pct = float(
            getattr(_get_settings().worker, "wait_jitter_pct", 0.0) or 0.0
        )
        seconds = _jittered_wait_seconds(_parse_wait_seconds(w), _jitter_pct)
        if seconds > 0:
            await asyncio.sleep(seconds)
            # Explicit pause ⇒ assume the screen changed during it
            # (timer ticks, popups animating in). Drop the framebuffer
            # cache so the next ``match`` / ``ocr`` doesn't reuse the
            # pre-wait frame.
            _invalidate = getattr(actions, "invalidate_frame_cache", None)
            if _invalidate is not None:
                _invalidate(instance_id)
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None
