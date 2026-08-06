from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from config.log_ansi import scenario_log_label
from config.log_context import set_log_context
from config.telemetry import safe_reason_label
from config.tracing import (
    set_span_attributes,
    task_duration_histogram,
    trace_id_hex_for_history,
    traced_root,
)
from dashboard.dashboard_events import publish_dashboard_event_async
from navigation.lifecycle_states import InstanceState
from scheduler.wake import wake_scheduler_async
from tasks.dsl_scenario import DslScenarioTask

logger = logging.getLogger(__name__)

_RUNNING_KEY_GLOBAL = "wos:queue:running"

def _running_key_for_instance(instance_id: str) -> str:
    return f"wos:queue:running:{instance_id}"


def _history_key_for_instance(instance_id: str) -> str:
    return f"wos:queue:history:{instance_id}"


def _redis_float_str(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.6g}"



if TYPE_CHECKING:
    from scheduler.queue import QueueItem
    from tasks.base import BaseTask, TaskResult
    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerTasksMixin(_Base):
    _cfg: Any
    _redis: Any
    _queue: Any
    _task_busy: Any

    async def _set_instance_state(self, state: InstanceState, *, error: str = "") -> None:
        # This mixin is used in multiple inheritance; implementations live in other mixins.
        return await super()._set_instance_state(state, error=error)  # type: ignore[misc]

    async def _ensure_account(self, player_id: str) -> None:
        return await super()._ensure_account(player_id)  # type: ignore[misc]

    async def _execute_task(self, item: QueueItem, task: BaseTask) -> TaskResult | None:
        return await super()._execute_task(item, task)  # type: ignore[misc]

    async def _drain_ui_commands(self) -> None:
        return await super()._drain_ui_commands()  # type: ignore[misc]

    async def _handle_failure(self, item: QueueItem, error: Exception) -> None:
        return await super()._handle_failure(item, error)  # type: ignore[misc]

    async def _run_one_queue_item(self, item: QueueItem, task: BaseTask) -> None:
        # Bind the player to the log context for the duration of this task.
        # ``inst`` is already bound at supervisor level; ``node`` is refreshed
        # by the screen detection / overlay analysis loop.
        if item.player_id:
            set_log_context(player=item.player_id)
        skip_account = getattr(task, "skip_account_check", False)
        self._task_busy.set()
        # Long deep-idle dwell drops the scrcpy stream to a low fps cap; lift it
        # NOW (not on the next rolling tick, up to 5 s away) so this task's first
        # post-tap capture has a fresh-frame budget. No-op unless the cap changed.
        self._rolling_deep_idle = False
        try:
            from config.capture_rate import scrcpy_max_fps_for_capture_interval

            self._bot_actions.set_scrcpy_max_fps(
                self._cfg.instance_id,
                scrcpy_max_fps_for_capture_interval(
                    getattr(self, "_capture_interval_override_s", None)
                ),
            )
        except Exception:
            logger.debug("task-start scrcpy fps restore failed", exc_info=True)
        started_at = float(time.time())

        state_key = f"wos:instance:{self._cfg.instance_id}:state"
        await self._set_instance_state(InstanceState.BUSY)
        # Publish currently-running job for UI. TTL avoids stale state on crashes.
        if self._redis is not None:
            try:
                import json
                inst_running_key = _running_key_for_instance(self._cfg.instance_id)

                running_payload: dict[str, object] = {
                    "task_id": item.task_id,
                    "task_type": item.task_type,
                    # Carry the scenario key for generic DSL envelopes
                    # (task_type="dsl_scenario") so the running-task dedup guard
                    # (`runner._task_already_running`) can match by logical type
                    # and not re-enqueue a scenario already in flight.
                    "dsl_scenario": item.dsl_scenario or "",
                    "player_id": item.player_id,
                    "priority": item.priority,
                    "instance_id": self._cfg.instance_id,
                    "region": item.region or "",
                    "started_at": started_at,
                }
                # Ranking breakdown of why this task won the pop (for `botctl why`).
                if item.rank_meta:
                    running_payload["rank_meta"] = item.rank_meta
                payload = json.dumps(running_payload, ensure_ascii=False)
                # Per-instance key (preferred).
                await self._redis.set(inst_running_key, payload, ex=180)  # type: ignore[union-attr]
                # Backward-compat: global "last running" snapshot.
                await self._redis.set(_RUNNING_KEY_GLOBAL, payload, ex=180)  # type: ignore[union-attr]
                await publish_dashboard_event_async(
                    self._redis,
                    topic="queue",
                    instance_id=self._cfg.instance_id,
                    reason="running",
                )
            except Exception:
                logger.debug("queue running key update failed", exc_info=True)
        # Which YAML is running (queue key == scenario stem for `DslScenarioTask`).
        scenario_for_job = ""
        if isinstance(task, DslScenarioTask):
            scenario_for_job = str(task.scenario_key or item.task_type or "").strip()

        # Track the current scenario for observability (botctl trace/state).
        if self._redis is not None and scenario_for_job:
            try:
                await self._redis.hset(
                    state_key,
                    mapping={
                        "last_active_scenario": scenario_for_job,
                        "last_active_scenario_priority": str(item.priority),
                        "last_active_scenario_player": item.player_id,
                        "last_active_scenario_step": str(
                            max(0, int(item.start_step_index or 0))
                        ),
                        "last_active_scenario_iter": "",
                    },
                )
            except Exception:
                logger.debug("failed to write last_active_scenario", exc_info=True)

        th_s = _redis_float_str(item.threshold)
        sc_s = _redis_float_str(item.score)
        # Queue JSON may omit floats on older items; overlay enqueue writes ``last_overlay_*`` hints.
        if self._redis is not None and (not th_s or not sc_s):
            try:
                snap_raw = await self._redis.hgetall(state_key)  # type: ignore[union-attr]
                snap: dict[str, str] = {}
                if isinstance(snap_raw, dict):
                    for k, v in snap_raw.items():
                        ks = k.decode() if isinstance(k, bytes) else str(k)
                        vs = v.decode() if isinstance(v, bytes) else str(v or "")
                        snap[ks] = vs
                if not th_s:
                    th_s = (snap.get("last_overlay_match_threshold") or "").strip()
                if not sc_s:
                    sc_s = (snap.get("last_overlay_match_score") or "").strip()
            except Exception:
                logger.debug("task start: overlay hint merge failed", exc_info=True)

        await self._redis.hset(  # type: ignore[union-attr]
            state_key,
            mapping={
                "current_task_id": item.task_id,
                "current_task_type": item.task_type,
                "current_task_player": item.player_id,
                "current_task_priority": str(int(item.priority)),
                "current_task_started_at": str(time.time()),
                "current_task_region": item.region or "",
                "current_task_threshold": th_s,
                "current_task_score": sc_s,
                "current_task_match_top_left_x": "" if item.match_top_left_x is None else str(item.match_top_left_x),
                "current_task_match_top_left_y": "" if item.match_top_left_y is None else str(item.match_top_left_y),
                "current_task_template_w": "" if item.template_w is None else str(item.template_w),
                "current_task_template_h": "" if item.template_h is None else str(item.template_h),
                "current_task_tap_match_x_pct": (
                    "" if item.tap_match_x_pct is None else f"{float(item.tap_match_x_pct):.6g}"
                ),
                "current_task_tap_match_y_pct": (
                    "" if item.tap_match_y_pct is None else f"{float(item.tap_match_y_pct):.6g}"
                ),
                "current_scenario": scenario_for_job,
            },
        )
        _task_result: TaskResult | None = None
        _task_error = ""
        _crashed = False
        _trace_name = (
            f"scenario.run {scenario_for_job}"
            if scenario_for_job
            else f"task.run {item.task_type}"
        )
        with traced_root(
            _trace_name,
            **{
                "wos.instance_id": self._cfg.instance_id,
                "wos.player_id": item.player_id,
                "wos.task_id": item.task_id,
                "wos.task_type": item.task_type,
                "wos.scenario": scenario_for_job or item.task_type,
                "wos.priority": int(item.priority),
                "wos.effective_priority": int(item.effective_priority or item.priority),
                "wos.region": item.region or "",
                "wos.start_step_index": int(item.start_step_index or 0),
            },
        ) as _task_span:
            logger.info(
                "Task start %s: id=%s type=%s player=%s prio=%s",
                self._cfg.instance_id,
                item.task_id,
                scenario_log_label(item.task_type),
                item.player_id,
                item.priority,
            )
            try:
                if not skip_account:
                    await self._ensure_account(item.player_id)
                _task_result = await self._execute_task(item, task)
                await self._drain_ui_commands()
                await self._reschedule_if_needed(item, _task_result)
                if _task_result is not None:
                    logger.info(
                        "Task done %s: id=%s success=%s next_run_at=%s",
                        self._cfg.instance_id,
                        item.task_id,
                        getattr(_task_result, "success", None),
                        getattr(_task_result, "next_run_at", None),
                    )
                else:
                    logger.info("Task done %s: id=%s (no result)", self._cfg.instance_id, item.task_id)
            except Exception as exc:
                _task_error = f"{type(exc).__name__}: {exc!s}"
                _task_span.record_exception(exc)
                _task_span.set_status(trace.Status(trace.StatusCode.ERROR, _task_error))
                await self._set_instance_state(InstanceState.CRASHED, error=f"unhandled task failure: {exc!s}")
                _crashed = True
                await self._handle_failure(item, exc)
            finally:
                _finished_at = float(time.time())
                # Emit terminal event: failure → task.failed, preempted reason →
                # task.preempted, otherwise task.finished. Single event per task
                # so the UI rendering can rely on a clean end marker.
                _metadata = (
                    (_task_result.metadata or {}) if _task_result is not None else {}
                )
                _reason = str(_metadata.get("reason") or "")
                if _task_error:
                    _terminal_event = "task.failed"
                elif _reason == "preempted_by_higher_priority" or _metadata.get(
                    "preempted"
                ):
                    _terminal_event = "task.preempted"
                else:
                    _terminal_event = "task.finished"
                _success = bool(_task_result.success) if _task_result is not None else not _task_error
                set_span_attributes(
                    _task_span,
                    **{
                        "wos.success": _success,
                        "wos.reason": _reason,
                        "wos.terminal_event": _terminal_event,
                        "wos.error": _task_error,
                        "wos.duration_s": max(0.0, _finished_at - started_at),
                    },
                )
                if not _success and not _task_error:
                    _task_span.set_status(
                        trace.Status(trace.StatusCode.ERROR, _reason or "task failed")
                    )
                # One histogram point per task: duration + outcome. The
                # ``_count`` series doubles as the scenario attempt counter,
                # so Grafana can rank failing scenarios without a second
                # instrument. ``error`` = unhandled exception, ``failed`` =
                # scenario reported failure, matching the span fields above.
                if _terminal_event == "task.preempted":
                    _outcome = "preempted"
                elif _task_error:
                    _outcome = "error"
                else:
                    _outcome = "success" if _success else "failed"
                try:
                    _attrs = {
                        "instance_id": self._cfg.instance_id,
                        "task_type": item.task_type,
                        "scenario": scenario_for_job or item.task_type,
                        "outcome": _outcome,
                    }
                    # WHY a scenario fails, not just that it does — lets Grafana
                    # split match_region_not_found (re-crop the template) from
                    # nav errors (fix the graph). Sanitised: free-text exception
                    # reprs collapse to one ``error`` bucket to cap cardinality.
                    if _outcome in ("failed", "error"):
                        _attrs["reason"] = (
                            safe_reason_label(_reason) or ("error" if _task_error else "failed")
                        )
                    task_duration_histogram().record(
                        max(0.0, _finished_at - started_at),
                        attributes=_attrs,
                    )
                except Exception:
                    logger.debug("task duration metric failed", exc_info=True)
                try:
                    await wake_scheduler_async(
                        self._redis,
                        {"cmd": "wake", "reason": _terminal_event, "task_id": item.task_id},
                    )
                except Exception:
                    logger.debug("wake_scheduler_async failed", exc_info=True)
                # Planner budget accounting: a planner-enqueued consumer/supply
                # carries its quota id + game-day period in ``args``. On success
                # bump the daily counter the allocator reads back and apply the
                # stamina estimate delta (see _apply_planner_post_consume).
                if _success and item.args:
                    await self._apply_planner_post_consume(item)
                await self._record_task_history(
                    item=item,
                    task=task,
                    started_at=started_at,
                    finished_at=_finished_at,
                    result=_task_result,
                    error=_task_error,
                )
                self._task_busy.clear()
                # Preserve CRASHED state + last_error so operators / UI can see the
                # terminal failure. The next successful task run will flip it back
                # to READY via the normal lifecycle.
                if not _crashed:
                    await self._set_instance_state(InstanceState.READY)
                    await self._maybe_enqueue_who_i_am_when_active_player_missing()
            if self._redis is not None:
                try:
                    inst_running_key = _running_key_for_instance(self._cfg.instance_id)
                    raw = await self._redis.get(inst_running_key)  # type: ignore[union-attr]
                    if raw:
                        import json

                        txt = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
                        data = json.loads(txt)
                        if str(data.get("task_id") or "") == item.task_id:
                            await self._redis.delete(inst_running_key)  # type: ignore[union-attr]
                            await publish_dashboard_event_async(
                                self._redis,
                                topic="queue",
                                instance_id=self._cfg.instance_id,
                                reason="finished",
                            )
                except Exception:
                    logger.debug("queue running key cleanup failed", exc_info=True)
            await self._redis.hset(  # type: ignore[union-attr]
                state_key,
                mapping={
                    "current_task_id": "",
                    "current_task_type": "",
                    "current_task_player": "",
                    "current_task_priority": "",
                    "current_task_started_at": "",
                    "current_task_region": "",
                    "current_task_threshold": "",
                    "current_task_score": "",
                    "current_task_match_top_left_x": "",
                    "current_task_match_top_left_y": "",
                    "current_task_template_w": "",
                    "current_task_template_h": "",
                    "current_task_tap_match_x_pct": "",
                    "current_task_tap_match_y_pct": "",
                    "current_scenario": "",
                    "last_overlay_match_threshold": "",
                    "last_overlay_match_score": "",
                    "last_overlay_match_region": "",
                },
            )

    async def _record_task_history(
        self,
        *,
        item: QueueItem,
        task: BaseTask,
        started_at: float,
        finished_at: float,
        result: TaskResult | None,
        error: str = "",
    ) -> None:
        if self._redis is None:
            return
        try:
            import json

            metadata = result.metadata if result is not None else {}
            success = bool(result.success) if result is not None else (not error)
            span_ctx = trace.get_current_span().get_span_context()
            trace_id = trace_id_hex_for_history(
                span_ctx=span_ctx,
                carrier=metadata if isinstance(metadata, dict) else None,
                fallback_seed=(
                    f"{self._cfg.instance_id}:{item.task_id}:{started_at:.6f}"
                ),
            )
            span_id = format(span_ctx.span_id, "016x") if span_ctx.span_id else ""
            row = {
                "task_id": item.task_id,
                "task_type": item.task_type,
                "scenario": str(getattr(task, "scenario_key", "") or item.task_type),
                "player_id": item.player_id,
                "instance_id": self._cfg.instance_id,
                "priority": item.priority,
                "region": item.region or "",
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_s": max(0.0, finished_at - started_at),
                "success": success,
                "error": error,
                "reason": str((metadata or {}).get("reason") or ""),
                "metadata": metadata or {},
                "trace_id": trace_id,
                "span_id": span_id,
            }
            # Durable shadow first: SQLite survives Redis flushes/downtime and
            # the helper swallows its own errors, so the Redis path is untouched.
            import asyncio

            from config.task_history_db import record_task_history

            await asyncio.to_thread(record_task_history, row)
            key = _history_key_for_instance(self._cfg.instance_id)
            await self._redis.lpush(key, json.dumps(row, ensure_ascii=False, default=str))  # type: ignore[union-attr]
            await self._redis.ltrim(key, 0, 49)  # type: ignore[union-attr]
            await self._redis.expire(key, 60 * 60 * 24 * 7)  # type: ignore[union-attr]
            await publish_dashboard_event_async(
                self._redis,
                topic="queue",
                instance_id=self._cfg.instance_id,
                reason="history",
            )
        except Exception:
            logger.debug("queue history update failed", exc_info=True)
        await self._record_action_feedback(item=item, result=result, error=error, finished_at=finished_at)

    async def _record_action_feedback(
        self,
        *,
        item: QueueItem,
        result: TaskResult | None,
        error: str,
        finished_at: float,
    ) -> None:
        """Feed a coordinator-dispatched task's outcome back to the planner.

        The march dispatcher tags its tasks with ``args.march_domain``; folding
        their success/failure into the per-player feedback hash is what lets the
        next march tick back off (or circuit-break) an action that keeps failing
        instead of re-queuing it blind. Success is used as the ``progressed``
        proxy — the before/after state-diff readers can refine it later.
        """
        if self._redis is None or not item.player_id:
            return
        args = item.args if isinstance(item.args, dict) else {}
        march_domain = str(args.get("march_domain") or "").strip()
        if not march_domain:
            return
        try:
            from games.wos.core.coordinator.feedback import Outcome
            from games.wos.core.coordinator.feedback_store import record_outcome

            metadata = result.metadata if result is not None else {}
            success = bool(result.success) if result is not None else (not error)
            reason = ""
            if not success:
                reason = str((metadata or {}).get("reason") or "").strip()
                if not reason:
                    # Free-text exceptions would fragment the same-reason streak;
                    # collapse them to one bucket so the breaker can still trip.
                    reason = "exception" if error else "failed"
            await record_outcome(
                self._redis,
                item.player_id,
                Outcome(
                    key=f"{march_domain}:run",
                    domain=march_domain,
                    progressed=success,
                    ts=finished_at,
                    reason=reason,
                ),
            )
        except Exception:
            logger.debug("action feedback record failed", exc_info=True)

    async def _reschedule_if_needed(self, item: QueueItem, result: TaskResult | None) -> None:
        if result is None or result.next_run_at is None or self._queue is None:
            return
        # ``datetime.timestamp()`` correctly handles both tz-aware and naive
        # datetimes (naive treated as local) and survives DST transitions.
        # The previous ``time.mktime(...timetuple())`` silently dropped tz
        # info, so an aware datetime would be misinterpreted as local.
        run_at = result.next_run_at.timestamp()
        # When a DSL scenario yields to a higher-priority task it returns
        # ``resume_from_step_index`` in its metadata (see
        # ``tasks/dsl_scenario_execute_mixin.py``). Forward it as
        # ``start_step_index`` so the re-enqueued slice continues where it
        # stopped instead of restarting at step 0.
        meta = result.metadata or {}
        raw_resume = meta.get("resume_from_step_index")
        try:
            resume_step = int(raw_resume) if isinstance(raw_resume, (int, float, str, bytes, bytearray)) else 0
        except (TypeError, ValueError):
            resume_step = 0
        if resume_step < 0:
            resume_step = 0
        # ``skip_if_duplicate`` prevents the yield-preempt ping-pong: two
        # same-signature items (e.g. ``who_i_am`` left over from a prior boot
        # plus the running one being rescheduled) would otherwise both sit in
        # queue. Device-level scenarios bypass the ``PREEMPT_MARGIN`` gate
        # (``top_is_device_level`` in ``_preempted_by_higher_priority``), so
        # each yields to the other and neither makes progress — the symptom is
        # an exploding ``yield_count:*`` set under a single instance.
        # Forward the WHOLE payload, not just the ranking/region subset. A
        # rescheduled item (yield-to-higher-priority, hand-pointer interrupt,
        # device-level preemption) must come back with everything that defines
        # WHAT it runs — most critically ``dsl_scenario`` (the generic
        # ``task_type="dsl_scenario"`` envelope is meaningless without it) and
        # ``args`` (planner reservation/quota markers, assigned heroes/troops,
        # stamina delta). Dropping those re-enqueued a scenario-less or
        # reservation-less husk; the worker then ran the wrong thing or leaked
        # the planner's hold. ``set_node`` / tap coords / match box round-trip
        # too so an overlay-derived tap still knows where to tap on resume.
        await self._queue.schedule(
            task_id=item.task_id,
            player_id=item.player_id,
            task_type=item.task_type,
            priority=item.priority,
            run_at=run_at,
            instance_id=self._cfg.instance_id,
            region=item.region,
            tap_x_pct=item.tap_x_pct,
            tap_y_pct=item.tap_y_pct,
            threshold=item.threshold,
            score=item.score,
            set_node=item.set_node,
            dsl_scenario=item.dsl_scenario,
            args=item.args,
            match_top_left_x=item.match_top_left_x,
            match_top_left_y=item.match_top_left_y,
            template_w=item.template_w,
            template_h=item.template_h,
            tap_match_x_pct=item.tap_match_x_pct,
            tap_match_y_pct=item.tap_match_y_pct,
            start_step_index=resume_step,
            skip_if_duplicate=True,
        )

    async def _apply_planner_post_consume(self, item: QueueItem) -> None:
        """Bump planner daily-quota counters + apply the stamina estimate delta
        after a planner-enqueued consumer/supply task succeeds.

        Both the stamina and resource planners read their per-(period, action)
        usage back from player state via the same ``quota_field(period, id)``
        (see ``games/wos/core/stamina`` and ``games/wos/core/resources``), so the
        worker bumps whichever marker pair the task carried. Without the resource
        pair, a capped ``daily_quota`` action's ``quota_used`` would stay 0 and it
        would re-dispatch without bound once the resource planner runs as a
        dispatcher. The stamina delta keeps the ``stamina`` estimate honest
        between OCR reads (the next read re-anchors it).

        Best-effort — a Redis flap must not fail an otherwise-successful task.
        """
        if self._redis is None or not item.args:
            return
        try:
            from games.wos.core.stamina.model import quota_field

            state_key = f"wos:player:{item.player_id}:state"
            for id_key, period_key in (
                ("stamina_quota_id", "stamina_period"),
                ("resource_action_id", "resource_period"),
            ):
                qid = item.args.get(id_key)
                qperiod = item.args.get(period_key)
                if qid and qperiod:
                    await self._redis.hincrby(
                        state_key, quota_field(str(qperiod), str(qid)), 1
                    )
            # Apply the spent/gained stamina delta now (next OCR read re-anchors).
            delta = int(item.args.get("stamina_delta") or 0)
            if delta:
                new = await self._redis.hincrby(state_key, "stamina", delta)
                if new < 0:
                    await self._redis.hset(state_key, "stamina", 0)
        except Exception:
            logger.debug("planner post-consume update failed", exc_info=True)
