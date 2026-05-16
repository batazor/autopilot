"""Cooperative preemption helpers for :class:`tasks.dsl_scenario.DslScenarioTask`.

Extracted from ``dsl_scenario.py`` so the main module stays a thin composition root.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from typing import Any

from config.log_ansi import scenario_log_label as _scen
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import _read_current_screen
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


class DslScenarioPreemptMixin:
    """Debug / rank-time cooperative yield checks."""

    redis_client: Any | None
    task_id: str
    scenario_key: str
    priority: int
    effective_priority: int
    _preempt_gen_at_start: int

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
            from services import get_settings

            q = RedisQueue(self.redis_client, get_settings())
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
