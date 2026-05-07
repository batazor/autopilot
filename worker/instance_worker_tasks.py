from __future__ import annotations

import logging
import time
from typing import Any

from fsm.states import InstanceState
from scheduler.queue import QueueItem
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask

logger = logging.getLogger(__name__)


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
        raise NotImplementedError

    async def _ensure_account(self, player_id: str) -> None:
        raise NotImplementedError

    async def _execute_task(self, item: QueueItem, task: BaseTask) -> TaskResult | None:
        raise NotImplementedError

    async def _drain_ui_commands(self) -> None:
        raise NotImplementedError

    async def _handle_failure(self, item: QueueItem, error: Exception) -> None:
        raise NotImplementedError

    async def _run_one_queue_item(self, item: QueueItem, task: BaseTask) -> None:
        skip_account = getattr(task, "skip_account_check", False)
        self._task_busy.set()

        state_key = f"wos:instance:{self._cfg.instance_id}:state"
        await self._set_instance_state(InstanceState.BUSY)
        # Which YAML is running (queue key == scenario stem for `DslScenarioTask`).
        scenario_for_job = ""
        if isinstance(task, DslScenarioTask):
            scenario_for_job = str(task.scenario_key or item.task_type or "").strip()
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
                "current_task_player": item.player_id,
                "current_task_started_at": str(time.time()),
                "current_task_region": item.region or "",
                "current_task_threshold": th_s,
                "current_task_score": sc_s,
                "current_scenario": scenario_for_job,
            },
        )
        logger.info(
            "Task start %s: id=%s type=%s player=%s prio=%s",
            self._cfg.instance_id,
            item.task_id,
            item.task_type,
            item.player_id,
            item.priority,
        )
        try:
            if not skip_account:
                await self._ensure_account(item.player_id)
            result = await self._execute_task(item, task)
            await self._drain_ui_commands()
            await self._reschedule_if_needed(item, result)
            if result is not None:
                logger.info(
                    "Task done %s: id=%s success=%s next_run_at=%s",
                    self._cfg.instance_id,
                    item.task_id,
                    getattr(result, "success", None),
                    getattr(result, "next_run_at", None),
                )
            else:
                logger.info("Task done %s: id=%s (no result)", self._cfg.instance_id, item.task_id)
        except Exception as exc:
            await self._set_instance_state(InstanceState.CRASHED, error=f"unhandled task failure: {exc!s}")
            await self._handle_failure(item, exc)
        finally:
            self._task_busy.clear()
            await self._set_instance_state(InstanceState.READY)
            await self._redis.hset(  # type: ignore[union-attr]
                state_key,
                mapping={
                    "current_task_player": "",
                    "current_task_started_at": "",
                    "current_task_region": "",
                    "current_task_threshold": "",
                    "current_task_score": "",
                    "current_scenario": "",
                    "last_overlay_match_threshold": "",
                    "last_overlay_match_score": "",
                    "last_overlay_match_region": "",
                },
            )

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

