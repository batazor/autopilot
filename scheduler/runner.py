from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import redis.asyncio as aioredis

from config.loader import get_settings
from scenarios.evaluator import ScenarioEvaluator
from scenarios.loader import ScenarioLoader
from scenarios.models import Scenario
from scheduler.optimizer import OptimizationInput, TaskOptimizer
from scheduler.queue import RedisQueue

logger = logging.getLogger(__name__)

_SCHEDULER_UI_QUEUE = "wos:ui:command:scheduler"


class SchedulerRunner:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis: aioredis.Redis | None = None  # type: ignore[type-arg]
        self._queue: RedisQueue | None = None
        self._optimizer = TaskOptimizer()
        self._evaluator = ScenarioEvaluator()
        scenarios_path = Path(__file__).parent.parent / "scenarios"
        self._scenario_loader = ScenarioLoader(scenarios_path)

    async def _connect(self) -> None:
        self._redis = aioredis.from_url(self._settings.redis.url)
        self._queue = RedisQueue(self._redis)
        self._scenario_loader.start_watching()

    async def _load_player_states(self) -> dict[str, dict[str, object]]:
        states: dict[str, dict[str, object]] = {}
        for inst in self._settings.instances:
            for player_id in inst.player_ids:
                key = f"wos:player:{player_id}:state"
                raw = await self._redis.hgetall(key)  # type: ignore[union-attr]
                state = {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in raw.items()
                }
                state["player_id"] = player_id
                states[player_id] = state
        return states

    async def _build_player_instance_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for inst in self._settings.instances:
            for player_id in inst.player_ids:
                mapping[player_id] = inst.instance_id
        return mapping

    async def _active_scenario_id(self, player_id: str) -> str | None:
        raw = await self._redis.get(f"wos:player:{player_id}:scenario")  # type: ignore[union-attr]
        if raw is None:
            return None
        s = raw.decode() if isinstance(raw, bytes) else raw
        return s.strip() if s.strip() else None

    @staticmethod
    def _filter_scenarios_for_player(
        player_id: str,
        active_sid: str | None,
        all_scenarios: list[Scenario],
    ) -> list[Scenario]:
        if not active_sid:
            return all_scenarios
        filtered = [s for s in all_scenarios if s.id == active_sid]
        if not filtered:
            logger.warning(
                "Player %s: scenario %r not found — using all scenarios",
                player_id,
                active_sid,
            )
            return all_scenarios
        return filtered

    async def _run_once(self) -> None:
        player_states = await self._load_player_states()
        scenarios = self._scenario_loader.load_all()
        player_instance_map = await self._build_player_instance_map()

        player_tasks: dict[str, list] = {}
        for player_id, state in player_states.items():
            active_sid = await self._active_scenario_id(player_id)
            scenario_list = self._filter_scenarios_for_player(
                player_id, active_sid, scenarios
            )
            all_tasks = []
            for scenario in scenario_list:
                tasks = self._evaluator.expand_to_tasks(scenario, state)
                all_tasks.extend(tasks)
            player_tasks[player_id] = all_tasks

        inp = OptimizationInput(
            player_tasks=player_tasks,
            player_instance_map=player_instance_map,
        )
        assigned = self._optimizer.optimize(inp)

        now = time.time()
        for player_id, tasks in assigned.items():
            instance_id = player_instance_map.get(player_id, "")
            for task in tasks:
                await self._queue.schedule(  # type: ignore[union-attr]
                    task_id=task.task_id,
                    player_id=player_id,
                    task_type=task.task_type,
                    priority=task.priority,
                    run_at=now,
                    instance_id=instance_id,
                )

        queue_items = await self._queue.peek_all()  # type: ignore[union-attr]
        logger.info("Scheduler: queued %d total items", len(queue_items))

    async def _handle_scheduler_ui_commands(self) -> None:
        assert self._redis is not None
        while True:
            raw = await self._redis.rpop(_SCHEDULER_UI_QUEUE)
            if raw is None:
                break
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if str(data.get("cmd")) == "optimize_now":
                try:
                    await self._run_once()
                except Exception:
                    logger.exception("optimize_now failed")

    async def run(self) -> None:
        await self._connect()
        interval = self._settings.scheduler.interval_seconds
        logger.info("Scheduler started, interval=%ds", interval)

        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("Scheduler loop error")

            end = time.monotonic() + interval
            while time.monotonic() < end:
                await self._handle_scheduler_ui_commands()
                remaining = end - time.monotonic()
                await asyncio.sleep(min(0.5, remaining) if remaining > 0 else 0.0)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runner = SchedulerRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
