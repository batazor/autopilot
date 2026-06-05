"""Cooperative preemption helpers for :class:`tasks.dsl_scenario.DslScenarioTask`.

Extracted from ``dsl_scenario.py`` so the main module stays a thin composition root.
"""
from __future__ import annotations

import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from config.log_ansi import scenario_log_label as _scen
from dashboard.redis_client import dsl_preempt_gen_key
from tasks.base import TaskResult
from tasks.dsl_scenario_helpers import _read_current_screen

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object

# Cooperative preemption knobs (ADR 0001 §5). Margin is large enough that two
# tasks within the same band don't ping-pong; immunity threshold caps the worst
# case so a high-priority chain can't starve a long-running scenario forever.
PREEMPT_MARGIN = 5_000
PREEMPT_MAX_YIELDS = 3
PREEMPT_YIELD_COUNT_TTL_SECONDS = 300

# Cache TTL for ``dsl_preempt_gen`` Redis reads.  A while_match iteration probes
# the preempt key once for the outer loop and once per inner step (via
# ``_inline_preempt_if_needed``) — at 5 Hz that's 3+ identical GETs per ~200 ms
# tick.  The key only changes when the debug UI bumps the counter, so a short
# cache window collapses the duplicates while keeping the worst-case delay for
# a "Run scenario now" press well below the user-visible threshold.
PREEMPT_GEN_CACHE_TTL_S = 0.25


def _yield_count_key(instance_id: str, task_id: str) -> str:
    return f"wos:instance:{instance_id}:yield_count:{task_id}"


class DslScenarioPreemptMixin(_Base):
    """Debug / rank-time cooperative yield checks."""

    redis_client: Any | None
    task_id: str
    scenario_key: str
    priority: int
    effective_priority: int
    _preempt_gen_at_start: int
    # ``(instance_id, primed_at_monotonic, preempted_int)`` — populated by
    # :meth:`_preempted_by_new_debug` so the per-inline-step probes inside a
    # while_match body share a single Redis round-trip per iteration.
    _preempt_gen_cache: tuple[str, float, int] | None

    async def _read_dsl_preempt_gen(self, instance_id: str) -> int | None:
        """Return current preempt gen, ``0`` if key is unset, or ``None`` on error.

        Distinguishing "unset" from "error" matters at scenario start: if the
        seed read fails and silently returns ``0`` while the live key is at
        ``N > 0``, every subsequent probe sees ``cur > start`` and the
        scenario aborts on its first step with ``dsl_preempted_debug``.
        """
        if self.redis_client is None:
            return 0
        try:
            raw = await self.redis_client.get(dsl_preempt_gen_key(instance_id))
        except Exception:
            logger.debug("dsl_preempt_gen read failed", exc_info=True)
            return None
        if raw is None:
            return 0
        try:
            s = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            return int(s)
        except (TypeError, ValueError):
            logger.debug("dsl_preempt_gen parse failed: %r", raw)
            return None

    async def _preempted_by_new_debug(self, instance_id: str) -> bool:
        if self.redis_client is None:
            return False
        cur = await self._read_dsl_preempt_gen(instance_id)
        if cur is None:
            # Transient Redis error — don't flip the preempt bit based on a
            # missing read, that would falsely preempt the scenario.
            return False
        # Lazy re-seed: if the start read failed (sentinel -1), adopt the
        # current value as the baseline now so subsequent bumps are detected.
        if int(self._preempt_gen_at_start) < 0:
            self._preempt_gen_at_start = cur
            self._preempt_gen_cache = (instance_id, time.monotonic(), 0)
            return False
        preempted = cur > int(self._preempt_gen_at_start)
        # Prime the inner-step cache so the immediately-following
        # ``_inline_preempt_if_needed`` probes (one per nested DSL step in a
        # while_match body) can short-circuit without re-reading Redis.
        self._preempt_gen_cache = (instance_id, time.monotonic(), int(preempted))
        return preempted

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
        if gap <= 0:
            return None
        try:
            from config.paths import repo_root
            from dsl.dsl_schema import dsl_scenario_yaml_device_level

            top_is_device_level = dsl_scenario_yaml_device_level(
                repo_root(), str(top.task_type or "")
            )
        except Exception:
            top_is_device_level = False
        if gap < PREEMPT_MARGIN and not top_is_device_level:
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
            next_run_at=datetime.now(tz=UTC),
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
        # Inner-step preempt probes fire once per nested DSL step — at while_match
        # cadence (~5 Hz) that's an extra GET on the same key for every inner
        # step in the loop body. Reuse the most recent outcome within a short
        # window: the debug "Run scenario now" press still preempts on the next
        # outer-loop probe (which always reads fresh via
        # :meth:`_preempted_by_new_debug`), so the worst-case reaction delay is
        # one while_match iteration — well below user-visible.
        cache = getattr(self, "_preempt_gen_cache", None)
        now = time.monotonic()
        if (
            isinstance(cache, tuple)
            and len(cache) == 3
            and cache[0] == instance_id
            and (now - cache[1]) < PREEMPT_GEN_CACHE_TTL_S
        ):
            if not bool(cache[2]):
                return None
        else:
            preempted = await self._preempted_by_new_debug(instance_id)
            self._preempt_gen_cache = (instance_id, now, int(bool(preempted)))
            if not preempted:
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
