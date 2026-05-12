from __future__ import annotations

import logging
import time
from typing import Any

from config.log_ansi import scenario_log_label
from config.log_context import set_log_context
from fsm.states import InstanceState
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY
from scheduler.queue import QueueItem
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask

logger = logging.getLogger(__name__)

_RUNNING_KEY_GLOBAL = "wos:queue:running"

# Scenarios that are allowed to interrupt ongoing work.  After any of these
# finishes we re-enqueue whatever scenario was running before it.
_HAND_POINTER_TASK_TYPES: frozenset[str] = frozenset({
    "hand_pointer",
    "hand_pointer_small",
    "hand_pointer_small_reverse",
})


def _running_key_for_instance(instance_id: str) -> str:
    return f"wos:queue:running:{instance_id}"


def _history_key_for_instance(instance_id: str) -> str:
    return f"wos:queue:history:{instance_id}"


def _redis_float_str(value: float | None) -> str:
    if value is None:
        return ""
    s = f"{float(value):.6g}"
    return s


class InstanceWorkerTasksMixin:
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
        started_at = float(time.time())

        state_key = f"wos:instance:{self._cfg.instance_id}:state"
        await self._set_instance_state(InstanceState.BUSY)
        # Publish currently-running job for UI. TTL avoids stale state on crashes.
        if self._redis is not None:
            try:
                import json
                inst_running_key = _running_key_for_instance(self._cfg.instance_id)

                payload = json.dumps(
                    {
                        "task_id": item.task_id,
                        "task_type": item.task_type,
                        "player_id": item.player_id,
                        "priority": item.priority,
                        "instance_id": self._cfg.instance_id,
                        "region": item.region or "",
                        "started_at": started_at,
                    },
                    ensure_ascii=False,
                )
                # Per-instance key (preferred).
                await self._redis.set(inst_running_key, payload, ex=180)  # type: ignore[union-attr]
                # Backward-compat: global "last running" snapshot.
                await self._redis.set(_RUNNING_KEY_GLOBAL, payload, ex=180)  # type: ignore[union-attr]
            except Exception:
                logger.debug("queue running key update failed", exc_info=True)
        # Which YAML is running (queue key == scenario stem for `DslScenarioTask`).
        scenario_for_job = ""
        if isinstance(task, DslScenarioTask):
            scenario_for_job = str(task.scenario_key or item.task_type or "").strip()

        # --- Hand-pointer interruption resume ---
        # If this is a hand-pointer task, capture whatever DSL scenario ran just
        # before it so we can re-enqueue it after the pointer is dismissed.
        _resume_scenario = ""
        _resume_priority = DEFAULT_SCENARIO_PRIORITY
        _resume_player = ""
        _resume_step = 0
        if self._redis is not None and item.task_type in _HAND_POINTER_TASK_TYPES:
            try:
                def _rd(raw: object) -> str:
                    return (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip()  # type: ignore[union-attr]
                fields = await self._redis.hmget(
                    state_key,
                    "last_active_scenario",
                    "last_active_scenario_priority",
                    "last_active_scenario_player",
                    "last_active_scenario_step",
                )
                _last, _pr_s, _pid_s, _step_s = [_rd(f) for f in fields]
                if _last and _last not in _HAND_POINTER_TASK_TYPES:
                    _resume_scenario = _last
                    try:
                        _resume_priority = int(_pr_s) if _pr_s else DEFAULT_SCENARIO_PRIORITY
                    except (ValueError, TypeError):
                        _resume_priority = DEFAULT_SCENARIO_PRIORITY
                    _resume_player = _pid_s
                    try:
                        _resume_step = int(_step_s) if _step_s else 0
                    except (ValueError, TypeError):
                        _resume_step = 0
                    # `building.upgrade` steps 0–1 open the chapter task + settle UI; resuming at the repeat
                    # block (step >= 2) skips them and template matches fail with the panel closed.
                    if _resume_scenario == "building.upgrade" and _resume_step >= 2:
                        _resume_step = 0
            except Exception:
                logger.debug("hand pointer resume: failed to read interrupted scenario", exc_info=True)
        # Track the current scenario so the next hand-pointer task can detect it.
        if self._redis is not None and scenario_for_job:
            try:
                await self._redis.hset(
                    state_key,
                    mapping={
                        "last_active_scenario": scenario_for_job,
                        "last_active_scenario_priority": str(item.priority),
                        "last_active_scenario_player": item.player_id,
                        "last_active_scenario_step": "0",
                    },
                )
            except Exception:
                logger.debug("hand pointer resume: failed to write last_active_scenario", exc_info=True)

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
        logger.info(
            "Task start %s: id=%s type=%s player=%s prio=%s",
            self._cfg.instance_id,
            item.task_id,
            scenario_log_label(item.task_type),
            item.player_id,
            item.priority,
        )
        _task_result: TaskResult | None = None
        _task_error = ""
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
            await self._set_instance_state(InstanceState.CRASHED, error=f"unhandled task failure: {exc!s}")
            await self._handle_failure(item, exc)
        finally:
            await self._record_task_history(
                item=item,
                task=task,
                started_at=started_at,
                finished_at=float(time.time()),
                result=_task_result,
                error=_task_error,
            )
            self._task_busy.clear()
            await self._set_instance_state(InstanceState.READY)
            # Re-enqueue only if the hand-pointer task actually matched and clicked
            # (not a false-positive overlay detection that failed the match guard).
            _hand_pointer_hit = _task_result is not None and str(
                (_task_result.metadata or {}).get("reason") or ""
            ) not in ("match_guard_failed", "match_region_not_found")
            _should_resume_hp = (
                _resume_scenario
                and item.task_type in _HAND_POINTER_TASK_TYPES
                and _hand_pointer_hit
                and self._queue is not None
            )
            if _should_resume_hp:
                try:
                    await self._queue.schedule(
                        task_id=f"resume:{self._cfg.instance_id}:{_resume_scenario}:{int(time.time())}",
                        player_id=_resume_player,
                        task_type=_resume_scenario,
                        priority=_resume_priority,
                        run_at=time.time(),
                        instance_id=self._cfg.instance_id,
                        start_step_index=_resume_step,
                        skip_if_duplicate=True,
                    )
                    logger.info(
                        "hand pointer: resuming scenario %s step=%s (prio=%s) after %s on %s",
                        scenario_log_label(_resume_scenario),
                        _resume_step,
                        _resume_priority,
                        item.task_type,
                        self._cfg.instance_id,
                    )
                except Exception:
                    logger.warning(
                        "hand pointer: failed to re-enqueue interrupted scenario %s",
                        _resume_scenario,
                        exc_info=True,
                    )
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
                except Exception:
                    logger.debug("queue running key cleanup failed", exc_info=True)
            await self._redis.hset(  # type: ignore[union-attr]
                state_key,
                mapping={
                    "current_task_id": "",
                    "current_task_type": "",
                    "current_task_player": "",
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
            }
            key = _history_key_for_instance(self._cfg.instance_id)
            await self._redis.lpush(key, json.dumps(row, ensure_ascii=False, default=str))  # type: ignore[union-attr]
            await self._redis.ltrim(key, 0, 49)  # type: ignore[union-attr]
            await self._redis.expire(key, 60 * 60 * 24 * 7)  # type: ignore[union-attr]
        except Exception:
            logger.debug("queue history update failed", exc_info=True)

    async def _reschedule_if_needed(self, item: QueueItem, result: TaskResult | None) -> None:
        if result is None or result.next_run_at is None or self._queue is None:
            return
        run_at = time.mktime(result.next_run_at.timetuple())
        await self._queue.schedule(
            task_id=item.task_id,
            player_id=item.player_id,
            task_type=item.task_type,
            priority=item.priority,
            run_at=run_at,
            instance_id=self._cfg.instance_id,
            region=item.region,
            threshold=item.threshold,
            score=item.score,
        )
