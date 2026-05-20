from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING

import redis as _redis_sync
import redis.asyncio as aioredis

from config.devices import player_ids_for_device_candidates
from config.paths import repo_root
from config.redis_health import ping_async_redis_or_exit
from dsl.cron_specs import (
    iter_cron_yaml_files_for_repo,
    resolve_cron_priority,
    resolve_cron_task_type,
)
from scheduler.optimizer import OptimizationInput, TaskOptimizer
from scheduler.ortools_executor import run_in_ortools_executor, shutdown_ortools_executor
from scheduler.queue import RedisQueue
from scheduler.wake import WAKE_CHANNEL, wake_scheduler

if TYPE_CHECKING:
    from config.loader import Settings
    from dsl.evaluator import ScenarioEvaluator
    from dsl.loader import ScenarioLoader
    from dsl.models import Scenario

logger = logging.getLogger(__name__)

_SCHEDULER_UI_QUEUE = WAKE_CHANNEL
_CRON_KEY = "wos:scheduler:cron:last_run"


class SchedulerRunner:
    def __init__(
        self,
        settings: Settings,
        scenario_loader: ScenarioLoader,
        optimizer: TaskOptimizer,
        evaluator: ScenarioEvaluator,
        *,
        redis: aioredis.Redis | None = None,  # type: ignore[type-arg]
        queue: RedisQueue | None = None,
        wake_sync: _redis_sync.Redis | None = None,
    ) -> None:
        self._settings = settings
        self._scenario_loader = scenario_loader
        self._redis = redis
        self._wake_sync = wake_sync
        self._queue = queue
        self._owns_redis = redis is None
        self._owns_wake_sync = wake_sync is None
        self._optimizer = optimizer
        self._evaluator = evaluator

    async def _connect(self) -> None:
        from config.redis_metrics import instrument_redis_client

        url = self._settings.redis.url
        if self._redis is None:
            self._redis = aioredis.from_url(url, socket_connect_timeout=5.0)
            instrument_redis_client(self._redis, component="scheduler")
            await ping_async_redis_or_exit(self._redis, url=url)
        if self._queue is None:
            self._queue = RedisQueue(self._redis, self._settings)
        if self._wake_sync is None:
            self._wake_sync = _redis_sync.Redis.from_url(url, socket_connect_timeout=5.0)
            instrument_redis_client(self._wake_sync, component="scheduler")
        self._scenario_loader.set_on_reload(self._on_scenarios_reloaded)
        self._scenario_loader.start_watching()

    def _on_scenarios_reloaded(self) -> None:
        """Fired from the watchdog observer thread when a scenario yaml changes.

        Publishes a wake so cron yaml edits trigger immediate re-optimization
        instead of waiting up to ``interval_seconds`` for the heartbeat tick.
        """
        client = self._wake_sync
        if client is None:
            return
        try:
            wake_scheduler(client, {"cmd": "wake", "reason": "scenarios_reloaded"})
        except Exception:
            logger.debug("wake on scenarios reload failed", exc_info=True)

    async def _disconnect_redis(self) -> None:
        client = self._redis
        sync_client = self._wake_sync
        self._redis = None
        self._wake_sync = None
        self._queue = None
        if sync_client is not None and self._owns_wake_sync:
            try:
                sync_client.close()
            except Exception:
                logger.debug("Scheduler wake-sync Redis close failed", exc_info=True)
        if client is None or not self._owns_redis:
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

    async def _task_already_running(
        self, *, instance_id: str, player_id: str, task_type: str
    ) -> bool:
        """True if a matching task is currently in flight on this instance.

        Reads the per-instance running key (``wos:queue:running:{instance_id}``)
        published by the worker for every task it pops (see
        ``worker/instance_worker_tasks.py``). The pending-queue dedup
        (``has_pending_duplicate``) only catches duplicates that are still
        in the sorted set — once the worker pops a long-running item the
        queue is empty and a fresh scheduler tick would otherwise re-enqueue
        the same logical task.
        """
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
        """Publish the cron spec at the right ``run_at`` and throttle re-enqueue.

        ``run_at`` honors the historical cadence: if ``recent_runs`` shows
        the task last fired ``T`` seconds ago and the cron interval is ``I``,
        we schedule ``run_at = now + max(0, I - T)`` so a restart doesn't
        re-fire a 4-hour cron the moment we boot. Cold start (no history)
        falls through to ``run_at = now`` — first run on a fresh setup
        shouldn't wait an interval to debut.

        A Redis throttle key (TTL=``interval_s``) still gates subsequent
        scheduler ticks (default cadence: 30s) so the same task isn't
        re-enqueued the moment the worker pops it. ``has_pending_duplicate``
        + ``_task_already_running`` cover the inverse: don't enqueue while one
        is already pending or in flight.
        """
        assert self._queue is not None and self._redis is not None
        if await self._task_already_running(
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

        last_run = await self._queue.last_run_at(
            instance_id=instance_id,
            task_type=task_type,
            player_id=player_id,
        )
        run_at = now if last_run is None else max(now, last_run + float(interval_s))

        throttle_key = (
            f"wos:scheduler:cron_throttle:{spec_slug}:{instance_id}:{player_id}"
        )
        acquired = bool(
            await self._redis.set(throttle_key, "1", nx=True, ex=int(interval_s))
        )
        if not acquired:
            return
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
            delay_s = max(0.0, run_at - now)
            logger.info(
                "Cron scheduled: %s (%s) %s for %s/%s at %s (delay=%.0fs, last_run=%s)",
                name,
                expr,
                task_type,
                instance_id,
                player_id,
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run_at)),
                delay_s,
                "never" if last_run is None
                else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_run)),
            )

    async def _record_recent_runs_history_depth(self, now: float) -> None:
        """Emit oldest-entry-age + size of ``recent_runs`` per instance.

        Best-effort — failures are debug-logged and silently dropped. Keeping
        the read out of the hot enqueue path (its own helper) means a Redis
        flap won't take down cron scheduling.
        """
        assert self._queue is not None
        from config.tracing import (
            recent_runs_history_age_histogram,
            recent_runs_history_size_histogram,
        )

        age_hist = recent_runs_history_age_histogram()
        size_hist = recent_runs_history_size_histogram()
        for inst in self._settings.instances:
            iid = inst.instance_id
            try:
                age = await self._queue.oldest_recent_run_age(
                    instance_id=iid, now=now
                )
                size = await self._redis.zcard(  # type: ignore[union-attr]
                    f"wos:instance:{iid}:recent_runs"
                )
            except Exception:
                logger.debug("recent_runs history depth probe failed", exc_info=True)
                continue
            if age is not None:
                age_hist.record(float(age), attributes={"instance_id": iid})
            size_hist.record(int(size or 0), attributes={"instance_id": iid})

    async def _run_cron_specs(self) -> None:
        """Enqueue cron-based jobs (no extra checks beyond cron)."""
        assert self._redis is not None and self._queue is not None
        now = time.time()
        await self._record_recent_runs_history_depth(now)
        root = repo_root()
        cron_ymls = iter_cron_yaml_files_for_repo(root)
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
            # Single source of truth for both task_type + priority fallbacks
            # — UI Cron Push uses the same helpers (``scenarios.cron_specs``)
            # so manual pushes match the scheduled enqueue exactly.
            task_type = resolve_cron_task_type(raw, yml)
            prio = resolve_cron_priority(raw.get("priority"))
            name = str(raw.get("name") or "").strip() or yml.stem
            when_current_screen = str(raw.get("when_current_screen") or "").strip().lower()
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
        # ``_connect`` runs before any tick, so ``_redis`` is always populated here.
        assert self._redis is not None
        states: dict[str, dict[str, object]] = {}
        for inst in self._settings.instances:
            for player_id in player_ids_for_device_candidates(
                inst.bluestacks_window_title,
                inst.instance_id,
            ):
                key = f"wos:player:{player_id}:state"
                raw = await self._redis.hgetall(key)
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
        assert self._redis is not None
        raw = await self._redis.get(f"wos:player:{player_id}:scenario")
        if raw is None:
            return None
        s = raw.decode() if isinstance(raw, bytes) else raw
        return s.strip() or None

    @staticmethod
    def _filter_scenarios_for_player(
        player_id: str,
        active_sid: str | None,
        all_scenarios: list[Scenario],
    ) -> list[Scenario]:
        if not active_sid:
            return all_scenarios
        # NOTE: `Scenario` exposes ``name`` (not ``id``); ``getattr`` keeps current
        # runtime semantics (no match → fallback to all_scenarios via the warning
        # branch below) until the active-scenario override key is reconciled.
        filtered = [s for s in all_scenarios if getattr(s, "id", None) == active_sid]
        if not filtered:
            logger.warning(
                "Player %s: scenario %r not found — using all scenarios",
                player_id,
                active_sid,
            )
            return all_scenarios
        return filtered

    async def _run_once(self) -> None:
        # ``_connect`` runs before any tick, so both ``_queue`` and ``_redis``
        # are always populated here.
        assert self._queue is not None
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
                # ``skip_if_duplicate`` only inspects the pending sorted set.
                # If the worker has already popped this logical task and is
                # mid-execution, the queue is empty and a fresh tick would
                # enqueue a second copy that runs back-to-back. Filter those
                # out by checking the per-instance running key as well.
                if await self._task_already_running(
                    instance_id=instance_id,
                    player_id=player_id,
                    task_type=task.task_type,
                ):
                    continue
                await self._queue.schedule(
                    task_id=task.task_id,
                    player_id=player_id,
                    task_type=task.task_type,
                    priority=task.priority,
                    run_at=now,
                    instance_id=instance_id,
                    skip_if_duplicate=True,
                    dedup_ignore_region=True,
                )

    async def _drain_wake_queue(self) -> bool:
        """Pop any remaining wake messages without blocking. Returns True if
        an ``optimize_now`` command was seen (caller may want to log/trace it)."""
        assert self._redis is not None
        saw_optimize_now = False
        while True:
            raw = await self._redis.rpop(_SCHEDULER_UI_QUEUE)
            if raw is None:
                return saw_optimize_now
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            # Tolerate non-dict payloads (a future producer could push a list
            # or scalar) — without this guard ``data.get("cmd")`` raises
            # AttributeError and kills the drain loop, stranding any later
            # wake messages until the next heartbeat.
            if not isinstance(data, dict):
                continue
            if str(data.get("cmd")) == "optimize_now":
                saw_optimize_now = True

    async def run(self) -> None:
        try:
            await self._connect()
            assert self._redis is not None
            interval = self._settings.scheduler.interval_seconds
            logger.info(
                "Scheduler started, heartbeat=%ds (event-driven on %s)",
                interval,
                _SCHEDULER_UI_QUEUE,
            )

            # Run once at boot so a cold-started worker sees its queue populated
            # without waiting for the first wake signal.
            try:
                await self._run_once()
            except Exception:
                logger.exception("Scheduler loop error (initial)")

            while True:
                # Block until any producer publishes a wake (state change,
                # task completion, UI command), or until the heartbeat fires.
                # Drain the rest so a burst of wakes collapses to one optimize.
                wake = await self._redis.blpop(_SCHEDULER_UI_QUEUE, timeout=interval)
                if wake is not None:
                    await self._drain_wake_queue()

                try:
                    await self._run_once()
                except Exception:
                    logger.exception("Scheduler loop error")
        finally:
            # Lets the next SchedulerRunner start a fresh filesystem watch (avoids duplicate FSEvents).
            self._scenario_loader.stop_watching()
            shutdown_ortools_executor(wait=False, cancel_futures=True)
            await self._disconnect_redis()


def main() -> None:
    from config.runtime_bootstrap import bootstrap_runtime_observability

    bootstrap_runtime_observability("scheduler")

    async def _run() -> None:
        from services import (
            aclose_app_services,
            get_scheduler_runner,
            init_app_services,
        )

        await init_app_services()
        try:
            runner = await get_scheduler_runner()
            await runner.run()
        finally:
            await aclose_app_services()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
