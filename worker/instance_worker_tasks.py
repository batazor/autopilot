from __future__ import annotations

import logging
import time
from typing import Any

from fsm.states import InstanceState
from scheduler.queue import QueueItem
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask

logger = logging.getLogger(__name__)


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
        # Which YAML is running (queue key == scenario stem for `DslScenarioTask`). Ad-skip taps
        # happen before `execute()` but are still "under" this job — show it in click approvals.
        scenario_for_job = ""
        if isinstance(task, DslScenarioTask):
            scenario_for_job = str(task.scenario_key or item.task_type or "").strip()
        await self._redis.hset(  # type: ignore[union-attr]
            state_key,
            mapping={
                "current_task_player": item.player_id,
                "current_task_started_at": str(time.time()),
                "current_task_region": item.region or "",
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
                    "current_scenario": "",
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
        )

