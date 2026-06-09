"""Loop/guard top-level step handlers for ``DslScenarioExecuteMixin``.

Each ``_exec_*_step`` method is the verbatim body of one ``if "<kind>" in
step:`` branch from the historical monolithic ``execute`` loop. The contract
is shared across all handlers:

- return ``None`` → the step finished; ``execute`` continues with the next
  top-level step (the historical ``continue``).
- return a :class:`TaskResult` → the scenario ends now; ``execute`` returns
  it unchanged (metadata is already ``fr.fin(...)``-wrapped here).

Loop-invariant context (resolved actions, area doc, scenario key, the
``fin`` / ``mark_step_done`` closures…) arrives via
:class:`tasks.dsl_scenario_exec_frame.ExecFrame`.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import (
    _action_pause_seconds,
    _BreakRepeat,
    _parse_wait_seconds,
    _read_current_screen,
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


class DslScenarioStepLoopsMixin(_Base):
    """``match`` / ``while_match`` / ``while_scroll`` / ``repeat`` handlers."""

    async def _exec_match_step(
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
        # ``match + steps`` = guarded block: matched → run ``steps``,
        # miss → run ``else`` (if any) and continue. The presence of
        # ``steps:`` is the explicit opt-in to soft semantics; bare
        # ``match:`` keeps its historical hard-gate behavior (abort
        # the scenario on miss) so existing gate-style usages stand.
        inner_steps = step.get("steps")
        else_steps = step.get("else")
        has_guarded_block = (
            isinstance(inner_steps, list) and bool(inner_steps)
        ) or (isinstance(else_steps, list) and bool(else_steps))
        if has_guarded_block:
            matched = bool(row.get("matched")) if row else False
            branch_steps = inner_steps if matched else else_steps
            branch_label = "steps" if matched else "else"
            if isinstance(branch_steps, list) and branch_steps:
                for inner_idx, inner in enumerate(branch_steps):
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
                        trace_path=(
                            f"{_resumable_step}.{branch_label}.{inner_idx}"
                        ),
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
            _trace_row(
                _resumable_step,
                step,
                "ok",
                matched=matched,
                branch=branch_label,
            )
            return None
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
            # ``match_guard_failed`` is a *failure* of intent: the
            # scenario declared "I need this region present" and it
            # wasn't. Marking the task ``success=True`` lumped these
            # rows together with real completions in queue history
            # (e.g. ``new_chapter`` shows ``reason=match_guard_failed``
            # but reads as success). Report it honestly so the UI
            # surfaces it as a failure; ``scenario_completed=False``
            # in metadata stays as the structural marker.
            return TaskResult(
                success=False,
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok")
        return None

    async def _exec_while_match_step(
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

        reg = str(step.get("while_match") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        try:
            max_iters = int(step.get("max", 20))
        except (TypeError, ValueError):
            max_iters = 20
        max_iters = max(0, max_iters)
        inner_steps = step.get("steps")
        if not isinstance(inner_steps, list):
            inner_steps = []

        # The *initial* probe may retry (default 1 attempt; opt in via
        # ``retry.attempts``) to absorb screen-settling lag after navigation.
        # Subsequent probes are single-shot — once we've matched once,
        # lack of a match means the work is done.
        #
        # YAML form:
        #   retry:
        #     attempts: 3
        #     interval: 500ms     # also accepts "0.5s" or raw seconds
        default_attempts = 1
        default_interval_s = 0.5
        # Scenario ``steps:`` are OR-semantics: each step tries; if a
        # ``while_match`` finds zero iterations, we just move to the
        # next step instead of failing the whole scenario. The previous
        # strict-by-default behavior for player-bound scenarios meant a
        # missing claim button (popup already closed, wrong screen) would
        # abort the scenario and pop an approval prompt — but the natural
        # idiom across our scenarios (``claim_trials``, ``mail.claim``,
        # ``vip_rewards``, etc.) is "try claim X, then claim Y, then …";
        # failure of one branch should not prevent the rest from running.
        # YAML can still set ``strict: true`` to opt into the
        # "must have done work" check for the rare gate-like step.
        default_strict = False
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
                # Force a fresh capture on each retry — _match_region reads
                # capture_screen_bgr_cached, so without invalidation every
                # attempt probes the same frame and the retry is a no-op.
                if attempt > 0 and hasattr(actions, "invalidate_frame_cache"):
                    with suppress(Exception):
                        actions.invalidate_frame_cache(instance_id)
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
            iter_path = f"{_resumable_step}.{iterations}"
            self._append_trace_row(
                iter_path, None, "iter", summary=f"iter {iterations}"
            )
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
                    scenario_key=key,
                    trace_path=f"{iter_path}.{inner_idx}",
                )
                if result is not None:
                    inner_result = result
                    break
            if inner_result is not None:
                break
            iterations += 1
            await self._publish_scenario_step_index(
                instance_id, _resumable_step, loop_iter=iterations
            )

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

        # ``else:`` — explicit fallback for the no-iterations case.
        # When provided, it bypasses the strict-reschedule path: the
        # scenario has declared how it wants to handle "icon never
        # appeared" itself (e.g. set a TTL, push another scenario).
        else_steps = step.get("else")
        if (
            iterations == 0
            and isinstance(else_steps, list)
            and else_steps
        ):
            else_result: TaskResult | None = None
            for else_idx, else_step in enumerate(else_steps):
                if not isinstance(else_step, dict):
                    continue
                else_result = await self._run_inline_step(
                    else_step,
                    actions=actions,
                    area_doc=area_doc,
                    repo_root=repo_root,
                    instance_id=instance_id,
                    dev_w=dev_w,
                    dev_h=dev_h,
                    scenario_key=key,
                    trace_path=f"{_resumable_step}.else.{else_idx}",
                )
                if else_result is not None:
                    break
            if else_result is not None:
                md = dict(else_result.metadata or {})
                _trace_row(
                    _resumable_step,
                    step,
                    "stopped",
                    reason=str(md.get("reason") or "else_stop"),
                )
                return TaskResult(
                    success=else_result.success,
                    next_run_at=else_result.next_run_at,
                    metadata=_fin(md, completed=False),
                )
            logger.info(
                "dsl_scenario: while_match else-branch ran scenario=%s region=%s",
                _scen(key),
                reg,
            )
            await _mark_top_level_step_done()
            _trace_row(_resumable_step, step, "ok", iterations=0, branch="else")
            return None

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
                next_run_at=datetime.now(tz=UTC) + timedelta(seconds=30),
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok", iterations=iterations)
        return None

    async def _exec_while_scroll_step(
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

        reg = str(step.get("while_scroll") or "").strip()
        await self._write_step_context(instance_id, scenario=key)
        direction = str(step.get("direction") or "up").strip().lower()
        try:
            delta = int(step.get("delta") or 350)
        except (TypeError, ValueError):
            delta = 350
        try:
            duration_ms = int(step.get("duration_ms") or 300)
        except (TypeError, ValueError):
            duration_ms = 300
        try:
            max_iters = int(step.get("max") or 30)
        except (TypeError, ValueError):
            max_iters = 30
        max_iters = max(0, max_iters)
        try:
            repeats_to_end = int(step.get("repeats_to_end") or 3)
        except (TypeError, ValueError):
            repeats_to_end = 3
        pause_s = _parse_wait_seconds(step.get("pause_ms") or step.get("pause") or "600ms")

        inner_steps = step.get("steps")
        if not isinstance(inner_steps, list):
            inner_steps = []

        pair = screen_region_by_name(
            area_doc,
            reg,
            state_flat=self._state_flat(),
            screen_id=(await _read_current_screen(instance_id, self.redis_client)) or None,
        )
        bbox = pair[1].get("bbox") if pair is not None else None
        if not isinstance(bbox, dict):
            logger.warning(
                "dsl_scenario: while_scroll region not found in area.json: %s (scenario=%s)",
                reg,
                _scen(key),
            )
            _trace_row(_resumable_step, step, "stopped", reason="region_not_found")
            return None

        try:
            px = int(round(float(bbox["x"]) / 100.0 * dev_w))
            py = int(round(float(bbox["y"]) / 100.0 * dev_h))
            pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
            ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
        except (KeyError, TypeError, ValueError):
            _trace_row(_resumable_step, step, "stopped", reason="invalid_bbox")
            return None
        if pw <= 0 or ph <= 0:
            _trace_row(_resumable_step, step, "stopped", reason="invalid_bbox")
            return None

        from analysis.scroll import ScrollEndDetector, fingerprint_region_bgr

        detector = ScrollEndDetector(repeats_to_end=repeats_to_end)
        _capture = getattr(actions, "capture_screen_bgr_cached", actions.capture_screen_bgr)
        _invalidate = getattr(actions, "invalidate_frame_cache", None)

        iterations = 0
        inner_result: TaskResult | None = None
        for i in range(max_iters):
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
            # First iter: process the currently-visible page before any swipe.
            if i > 0 and direction and delta > 0:
                ok = await asyncio.to_thread(
                    actions.swipe_direction,
                    instance_id,
                    direction=direction,
                    delta=delta,
                    duration_ms=duration_ms,
                )
                if not ok:
                    _trace_row(_resumable_step, step, "stopped", reason="swipe_not_approved")
                    return TaskResult(
                        success=False,
                        next_run_at=None,
                        metadata=_fin(
                            {"scenario": key, "reason": "swipe_not_approved"},
                            completed=False,
                        ),
                    )
                if pause_s > 0:
                    await asyncio.sleep(_action_pause_seconds(pause_s))

            iter_path = f"{_resumable_step}.{i}"
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
                    scenario_key=key,
                    trace_path=f"{iter_path}.{inner_idx}",
                )
                if result is not None:
                    inner_result = result
                    break
            if inner_result is not None:
                break

            # Fingerprint AFTER inner steps so post-claim state is what we compare.
            if _invalidate is not None:
                with suppress(Exception):
                    _invalidate(instance_id)
            image_bgr = await asyncio.to_thread(_capture, instance_id)
            patch = image_bgr[py:py + ph, px:px + pw]
            fp = fingerprint_region_bgr(patch)
            detector.push(fp)
            iterations += 1
            if detector.is_the_end():
                break

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
        logger.info(
            "dsl_scenario: while_scroll done scenario=%s region=%s iterations=%d",
            _scen(key),
            reg,
            iterations,
        )
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok", iterations=iterations)
        return None

    async def _exec_repeat_step(
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
            return None

        until_any_list: list[str] = []
        if isinstance(until_any, list):
            until_any_list = [
                str(x or "").strip()
                for x in until_any
                if str(x or "").strip()
            ]

        iter_idx_total = 0
        for iter_idx in range(max_iters):
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
            iter_path = f"{_resumable_step}.{iter_idx}"
            self._append_trace_row(
                iter_path, None, "iter", summary=f"iter {iter_idx}"
            )
            iter_idx_total = iter_idx + 1
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
                        scenario_key=key,
                        trace_path=f"{iter_path}.{inner_idx}",
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
        await _mark_top_level_step_done()
        _trace_row(_resumable_step, step, "ok", iterations=iter_idx_total)
        return None
