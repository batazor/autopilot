from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import redis.asyncio as aioredis

from config.devices import player_ids_for_device_candidates
from config.loader import get_settings
from config.logging_stdout import setup_stdout_logging
from config.redis_health import ping_async_redis_or_exit
from scenarios.cron_specs import iter_cron_yaml_files
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY
from scenarios.evaluator import ScenarioEvaluator
from scenarios.loader import ScenarioLoader
from scenarios.models import Scenario
from scheduler.optimizer import OptimizationInput, TaskOptimizer
from scheduler.ortools_executor import run_in_ortools_executor, shutdown_ortools_executor
from scheduler.queue import RedisQueue

logger = logging.getLogger(__name__)

_SCHEDULER_UI_QUEUE = "wos:ui:command:scheduler"
_CRON_KEY = "wos:scheduler:cron:last_run"

def resolve_cron_priority(raw: object) -> int:
    """Coerce a cron YAML ``priority`` to an int, falling back to the unified
    :data:`scenarios.dsl_schema.DEFAULT_SCENARIO_PRIORITY` when missing.

    Cron has no enqueue-path of its own — it just feeds the same queue as
    overlay pushes and the per-task DSL constructor, so it shares the same
    default. Handles three foot-guns of the previous ``int(raw.get("priority")
    or 1)`` idiom: ``None`` (missing field), ``bool`` (``True``/``False`` are
    ints in Python — silently passed as 0 or 1), and bad string values. ``0``
    is a valid explicit priority distinct from "missing" and is preserved.
    """
    if raw is None or isinstance(raw, bool):
        return DEFAULT_SCENARIO_PRIORITY
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SCENARIO_PRIORITY


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
        url = self._settings.redis.url
        self._redis = aioredis.from_url(url, socket_connect_timeout=5.0)
        await ping_async_redis_or_exit(self._redis, url=url)
        self._queue = RedisQueue(self._redis)
        self._scenario_loader.start_watching()

    async def _disconnect_redis(self) -> None:
        client = self._redis
        self._redis = None
        self._queue = None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:
            logger.debug("Scheduler Redis aclose failed", exc_info=True)

    async def _instance_current_screen(self, instance_id: str) -> str:
        assert self._redis is not None
        raw = await self._redis.hget(f"wos:instance:{instance_id}:state", "current_screen")
        if raw is None:
            return ""
        s = raw.decode() if isinstance(raw, bytes) else str(raw)
        return s.strip()

    @staticmethod
    def _cron_due(expr: str, now: float) -> bool:
        """Minimal cron matcher supporting:

        - "*/N * * * *"  (every N minutes)
        - "M */H * * *"  (minute M, every H hours)
        """
        expr = (expr or "").strip().strip('"').strip("'")
        parts = expr.split()
        if len(parts) != 5:
            return False
        minute, hour, _dom, _mon, _dow = parts
        lt = time.localtime(now)
        m = lt.tm_min
        h = lt.tm_hour

        if minute.startswith("*/") and hour == "*":
            try:
                n = int(minute[2:])
            except ValueError:
                return False
            return n > 0 and (m % n == 0)

        if hour.startswith("*/"):
            try:
                hh = int(hour[2:])
                mm = int(minute)
            except ValueError:
                return False
            return hh > 0 and (m == mm) and (h % hh == 0)

        return False

    @staticmethod
    def _cron_interval_seconds(expr: str) -> int | None:
        """Return the interval for cron shapes supported by this scheduler.

        The scheduler intentionally supports only the two shapes used by our
        maintenance specs. For those, we can keep a concrete future queue item
        instead of relying on hitting the exact cron minute.
        """
        expr = (expr or "").strip().strip('"').strip("'")
        parts = expr.split()
        if len(parts) != 5:
            return None
        minute, hour, _dom, _mon, _dow = parts

        if minute.startswith("*/") and hour == "*":
            try:
                n = int(minute[2:])
            except ValueError:
                return None
            return n * 60 if n > 0 else None

        if hour.startswith("*/"):
            try:
                hh = int(hour[2:])
                int(minute)
            except ValueError:
                return None
            return hh * 60 * 60 if hh > 0 else None

        return None

    async def _cron_task_running(self, *, instance_id: str, player_id: str, task_type: str) -> bool:
        assert self._redis is not None
        raw = await self._redis.get(f"wos:queue:running:{instance_id}")
        if raw is None:
            return False
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        if str(data.get("task_type") or "") != task_type:
            return False
        running_player = str(data.get("player_id") or "")
        return not player_id or running_player == player_id

    async def _ensure_interval_cron_item(
        self,
        *,
        name: str,
        spec_slug: str,
        expr: str,
        task_type: str,
        priority: int,
        instance_id: str,
        player_id: str,
        interval_s: int,
        now: float,
    ) -> None:
        """Publish the cron spec immediately and throttle re-enqueue by interval.

        Every cron-driven scenario lands in the queue with ``run_at=now`` on
        first sight so a cold-started worker doesn't wait a full ``interval_s``
        for the first run. A Redis throttle key (TTL=``interval_s``) gates
        subsequent scheduler ticks (default cadence: 30s) so the same task
        isn't re-enqueued the moment the worker pops it. ``has_pending_duplicate``
        + ``_cron_task_running`` cover the inverse: don't enqueue while one is
        already pending or in flight.
        """
        assert self._queue is not None and self._redis is not None
        if await self._cron_task_running(
            instance_id=instance_id,
            player_id=player_id,
            task_type=task_type,
        ):
            return
        if await self._queue.has_pending_duplicate(
            player_id=player_id,
            task_type=task_type,
            region=None,
            instance_id=instance_id,
            ignore_region=True,
        ):
            return

        throttle_key = (
            f"wos:scheduler:cron_throttle:{spec_slug}:{instance_id}:{player_id}"
        )
        acquired = bool(
            await self._redis.set(throttle_key, "1", nx=True, ex=int(interval_s))
        )
        if not acquired:
            return
        run_at = now
        enqueued = await self._queue.schedule(
            task_id=f"cron:{spec_slug}:{player_id}:{int(run_at)}",
            player_id=player_id,
            task_type=task_type,
            priority=priority,
            run_at=run_at,
            instance_id=instance_id,
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
        if enqueued:
            logger.info(
                "Cron scheduled: %s (%s) %s for %s/%s at %s",
                name,
                expr,
                task_type,
                instance_id,
                player_id,
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run_at)),
            )

    async def _run_cron_specs(self) -> None:
        """Enqueue cron-based jobs (no extra checks beyond cron)."""
        assert self._redis is not None and self._queue is not None
        now = time.time()
        scenarios_root = Path(__file__).resolve().parent.parent / "scenarios"
        cron_ymls = iter_cron_yaml_files(scenarios_root)
        if not cron_ymls:
            return

        import yaml

        for yml in cron_ymls:
            try:
                raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                logger.exception("Cron spec load failed: %s", yml)
                continue
            if not isinstance(raw, dict):
                continue
            enabled = bool(raw.get("enabled", True))
            if not enabled:
                continue
            expr = str(raw.get("cron") or "").strip()
            task_type = str(raw.get("task") or raw.get("task_type") or "").strip()
            prio = resolve_cron_priority(raw.get("priority"))
            name = str(raw.get("name") or "").strip() or yml.stem
            when_current_screen = str(raw.get("when_current_screen") or "").strip().lower()
            if not task_type:
                # One file per cron job: default queue type is the YAML stem (e.g. `check_main_city.yaml`
                # → `check_main_city`). Override with explicit `task:` when it must differ from the stem.
                task_type = yml.stem
            if not expr or not task_type:
                continue

            # Use `name` as the human identifier; normalize to a slug for keys.
            spec_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-") or yml.stem
            interval_s = self._cron_interval_seconds(expr)
            for inst in self._settings.instances:
                if when_current_screen:
                    current_screen = await self._instance_current_screen(inst.instance_id)
                    if when_current_screen in {"unknown", "none", "empty"}:
                        if current_screen:
                            continue
                    else:
                        if current_screen.lower() != when_current_screen:
                            continue
                player_ids = player_ids_for_device_candidates(
                    inst.bluestacks_window_title,
                    inst.instance_id,
                )
                for player_id in player_ids:
                    if interval_s is not None:
                        await self._ensure_interval_cron_item(
                            name=name,
                            spec_slug=spec_slug,
                            expr=expr,
                            task_type=task_type,
                            priority=prio,
                            instance_id=inst.instance_id,
                            player_id=player_id,
                            interval_s=interval_s,
                            now=now,
                        )
                        continue

                    if not self._cron_due(expr, now):
                        continue
                    # Once-per-minute guard (scheduler ticks faster than cron granularity).
                    guard = f"{spec_slug}:{inst.instance_id}:{player_id}:{int(now // 60)}"
                    if await self._redis.hget(_CRON_KEY, guard):  # type: ignore[arg-type]
                        continue
                    await self._redis.hset(_CRON_KEY, guard, "1")
                    await self._redis.expire(_CRON_KEY, 60 * 60 * 24)

                    await self._queue.schedule(
                        task_id=f"cron:{spec_slug}:{player_id}:{int(now)}",
                        player_id=player_id,
                        task_type=task_type,
                        priority=prio,
                        run_at=now,
                        instance_id=inst.instance_id,
                        skip_if_duplicate=True,
                        dedup_ignore_region=True,
                    )
                    logger.info(
                        "Cron enqueued: %s (%s) %s for %s/%s",
                        name,
                        expr,
                        task_type,
                        inst.instance_id,
                        player_id,
                    )

    async def _load_player_states(self) -> dict[str, dict[str, object]]:
        states: dict[str, dict[str, object]] = {}
        for inst in self._settings.instances:
            for player_id in player_ids_for_device_candidates(
                inst.bluestacks_window_title,
                inst.instance_id,
            ):
                key = f"wos:player:{player_id}:state"
                raw = await self._redis.hgetall(key)  # type: ignore[union-attr]
                state = {
                    (k.decode() if isinstance(k, bytes) else k): (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in raw.items()
                }
                state["player_id"] = player_id
                states[player_id] = state
        return states

    async def _build_player_instance_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for inst in self._settings.instances:
            for player_id in player_ids_for_device_candidates(
                inst.bluestacks_window_title,
                inst.instance_id,
            ):
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
        await self._run_cron_specs()
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
        # OR-Tools solve is synchronous. Use a dedicated single-worker pool (not the default
        # asyncio thread pool) so solves are serialized and the rest of the app stays responsive.
        loop = asyncio.get_running_loop()
        assigned = await run_in_ortools_executor(
            loop,
            self._optimizer.optimize,
            inp,
        )

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
        try:
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
        finally:
            # Lets the next SchedulerRunner start a fresh filesystem watch (avoids duplicate FSEvents).
            self._scenario_loader.stop_watching()
            shutdown_ortools_executor(wait=False, cancel_futures=True)
            await self._disconnect_redis()


def main() -> None:
    setup_stdout_logging()
    runner = SchedulerRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
