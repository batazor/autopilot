from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np
import redis.asyncio as aioredis

from actions.tap import BotActions
from capture.adb_screencap import DEFAULT_ADB_BIN
from config.devices import player_ids_for_device, player_ids_for_device_candidates
from config.loader import InstanceConfig, get_settings
from config.reference_naming import reference_file_basename, reference_png_abs_path
from fsm.states import InstanceState
from navigation.detector import ScreenDetector
from scheduler.queue import QueueItem, RedisQueue
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask
from worker.instance_worker_blocking import InstanceWorkerBlockingMixin
from worker.instance_worker_health import InstanceWorkerHealthMixin
from worker.instance_worker_overlay import InstanceWorkerOverlayMixin
from worker.instance_worker_redis import InstanceWorkerRedisMixin
from worker.instance_worker_rolling import InstanceWorkerRollingMixin
from worker.instance_worker_screen import (
    InstanceWorkerScreenDetectMixin,
    InstanceWorkerScreenMixin,
)
from worker.instance_worker_tasks import InstanceWorkerTasksMixin
from worker.instance_worker_ui import InstanceWorkerUiMixin

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, type] = {}

# Redis hash for UI/monitoring.
_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"

# DSL scenarios pushed once per instance start. Each entry must be a key resolvable
# by `DslScenarioTask` (i.e. a YAML file under `scenarios/**/{key}.yaml`).
#
# Priority band: above routine state-aware overlays (assign_worker, read_mail_gifts,
# chapter_task_router et al. at 70_000–80_000) so identity is established before any
# action is attributed to a player. Stays below tutorial overlays (skip=85_000,
# hand=86_000) — those must preempt seeds because navigating to chief_profile while
# a tutorial is active would tap the tutorial UI instead.
_STARTUP_SEED_TASKS: tuple[tuple[str, int], ...] = (
    # ``where_i_am`` first: figure out the current screen before anything
    # tries to navigate or tap. ``who_i_am`` then takes the established
    # current_screen as a starting point for its FSM hop to chief_profile
    # (and its OCR step is what populates ``active_player``).
    ("where_i_am", 83_000),
    ("who_i_am", 82_000),
)
_SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES = 3
_SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS = 2.0


class InstanceWorker(
    InstanceWorkerUiMixin,
    InstanceWorkerOverlayMixin,
    InstanceWorkerTasksMixin,
    InstanceWorkerBlockingMixin,
    InstanceWorkerRedisMixin,
    InstanceWorkerHealthMixin,
    InstanceWorkerScreenDetectMixin,
    InstanceWorkerScreenMixin,
    InstanceWorkerRollingMixin,
):
    _SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES = _SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES
    _SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS = _SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS

    def __init__(self, instance_config: InstanceConfig) -> None:
        self._cfg = instance_config
        self._settings = get_settings()
        self._redis: aioredis.Redis | None = None  # type: ignore[type-arg]
        self._queue: RedisQueue | None = None
        self._claims: Any | None = None
        self._bot_actions = BotActions()
        self._player_fsms: dict[str, Any] = {}
        self._instance_state = InstanceState.READY
        self._ui_paused = False
        self._task_busy = asyncio.Event()
        self._rolling_snap_seq = 0
        self._last_current_screen: str | None = None
        self._last_detected_screen: str | None = None
        self._last_detected_screen_at: float = 0.0
        self._screen_unknown_streak = 0
        self._screen_detector = ScreenDetector()
        self._overlay_rule_eval_state: dict[str, float] = {}
        # Avoid asyncio default executor shutdown races during app stop/reload.
        self._blocking_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix=f"wos-{self._cfg.instance_id}-",
        )
        self._rolling_snapshot_task: asyncio.Task[None] | None = None
        self._restart_listener_task: asyncio.Task[None] | None = None
        self._blocking_executor_live: bool = True
        self._stopping: bool = False
        self._task_registry = _TASK_REGISTRY

    # Legacy hook removed: mail gift check will be a DSL scenario when needed.

    def _worker_adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref if pref else DEFAULT_ADB_BIN

    def _build_task(self, item: QueueItem) -> BaseTask | None:
        factory = _TASK_REGISTRY.get(item.task_type)
        if factory is None:
            return DslScenarioTask(
                task_id=item.task_id,
                player_id=item.player_id,
                priority=item.priority,
                scenario_key=item.task_type,
                tap_region=item.region or "",
                tap_x_pct=item.tap_x_pct,
                tap_y_pct=item.tap_y_pct,
                start_step_index=item.start_step_index,
                redis_client=self._redis,
                effective_priority=item.effective_priority or item.priority,
            )
        return factory(  # type: ignore[return-value]
            task_id=item.task_id,
            player_id=item.player_id,
            priority=item.priority,
            redis_client=self._redis,
        )

    async def _execute_task(self, item: QueueItem, task: BaseTask) -> TaskResult | None:
        try:
            if task.is_cooperative:
                claimed = await self._claims.claim(  # type: ignore[union-attr]
                    task.task_type, item.player_id, ttl=300
                )
                if not claimed:
                    logger.info("Cooperative task %s already claimed, skipping", task.task_type)
                    return None

            result = await asyncio.wait_for(
                task.execute(self._cfg.instance_id),
                timeout=self._settings.worker.task_timeout_seconds,
            )

            return result

        except TimeoutError:
            logger.error("Task %s timed out on %s", item.task_id, self._cfg.instance_id)
            return None

        except Exception as exc:
            logger.exception("Task %s failed: %s", item.task_id, exc)
            return None

        finally:
            if task.is_cooperative:
                await self._claims.release(task.task_type, item.player_id)  # type: ignore[union-attr]

    async def _seed_startup_tasks(self) -> None:
        """Enqueue boot-time DSL scenarios (one per fresh worker run, per device).

        Examples: ``who_i_am`` — figure out which account is currently active so the
        scheduler / approval UI can label state correctly. Skips duplicates already
        pending in the queue.

        Seeds run with no extra delay: ``_startup_overlay_tick`` runs synchronously
        before this and queues any tutorials/popups/banners visible on the first
        frame; those preempt seeds via the priority hierarchy (tutorials at 85-86k
        > seeds at 82-83k > routine at 70-80k).
        """
        if self._queue is None:
            return
        run_at = time.time()
        for scenario_key, priority in _STARTUP_SEED_TASKS:
            # Remove stale items from previous runs — they carry an old run_at (past
            # timestamp) and would be immediately runnable, bypassing the delay.
            try:
                removed = await self._queue.remove_by_task_type(
                    scenario_key, self._cfg.instance_id
                )
                if removed:
                    logger.info(
                        "Startup seed: removed %d stale %r item(s) from queue",
                        removed, scenario_key,
                    )
            except Exception:
                logger.exception(
                    "startup seed: failed to remove stale items for %s", scenario_key
                )

            task_id = (
                f"startup:{self._cfg.instance_id}:{scenario_key}:"
                f"device:{uuid.uuid4().hex[:8]}"
            )
            try:
                enqueued = await self._queue.schedule(
                    task_id=task_id,
                    player_id="",
                    task_type=scenario_key,
                    priority=priority,
                    run_at=run_at,
                    instance_id=self._cfg.instance_id,
                )
            except Exception:
                logger.exception(
                    "startup seed enqueue failed: instance=%s scenario=%s player=%s",
                    self._cfg.instance_id,
                    scenario_key,
                    "(device)",
                )
                continue
            if enqueued:
                logger.info(
                    "Startup seed enqueued: %s for %s/%s (prio=%d)",
                    scenario_key,
                    self._cfg.instance_id,
                    "(device)",
                    priority,
                )

    async def _handle_failure(self, item: QueueItem, error: Exception) -> None:
        logger.error("Unhandled failure for task %s: %s", item.task_id, error)

    async def run(self) -> None:
        await self._connect()
        logger.info("Worker started for instance %s", self._cfg.instance_id)
        logger.info(
            "ADB config for %s: serial=%s adb_executable=%s",
            self._cfg.instance_id,
            self._cfg.bluestacks_window_title,
            self._worker_adb_bin(),
        )
        repo_root = Path(__file__).resolve().parent.parent
        rolling_path = reference_png_abs_path(
            repo_root,
            reference_file_basename(None, self._cfg.instance_id),
            self._cfg.instance_id,
        )
        logger.info(
            "Rolling preview %s: interval=%.2fs path=%s",
            self._cfg.instance_id,
            float(self._settings.worker.device_reference_snapshot_interval_seconds),
            rolling_path,
        )
        try:
            try:
                await self._run_blocking(self._ensure_whiteout_at_worker_start)
            except Exception:
                logger.exception(
                    "Whiteout foreground check/launch failed for instance %s", self._cfg.instance_id
                )
            await self._startup_overlay_tick()
            await self._seed_startup_tasks()
            # Legacy: page detect disabled (YAML-only mode).
            self._rolling_snapshot_task = asyncio.create_task(
                self._device_reference_snapshot_loop(),
                name=f"refsnap-{self._cfg.instance_id}",
            )
            self._restart_listener_task = asyncio.create_task(
                self._run_restart_event_listener(),
                name=f"restart-events-{self._cfg.instance_id}",
            )
            last_heartbeat = 0.0

            try:
                while True:
                    # Heartbeat for UI: lets us distinguish "stale restarting" from "actually down".
                    now_m = time.monotonic()
                    if now_m - last_heartbeat >= 2.0:
                        try:
                            await self._redis.hset(  # type: ignore[union-attr]
                                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                                "last_seen_at",
                                str(time.time()),
                            )
                        except Exception:
                            logger.debug("Failed to write last_seen_at heartbeat", exc_info=True)
                        last_heartbeat = now_m

                    await self._drain_ui_commands()
                    while self._ui_paused:
                        await self._drain_ui_commands()
                        await asyncio.sleep(0.3)

                    item = await self._pop_next_task()
                    if item is None:
                        # Block up to 2s but wake immediately when UI pushes to the command list
                        # (e.g. debug "Run scenario now" sends ``wake`` after zadding the task).
                        if self._redis is not None:
                            cmd_key = f"wos:ui:command:{self._cfg.instance_id}"
                            raw_bp = await self._redis.brpop(cmd_key, timeout=2)  # type: ignore[union-attr]
                            if raw_bp:
                                _, payload = raw_bp
                                await self._handle_ui_command(payload)
                                await self._drain_ui_commands()
                        else:
                            await asyncio.sleep(2.0)
                        continue
                    item = await self._resolve_queue_item_player(item)

                    task = self._build_task(item)
                    if task is None:
                        continue

                    await self._run_one_queue_item(item, task)
                    await self._overlay_tick_now(reason=f"after task {item.task_type}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._set_instance_state(
                    InstanceState.CRASHED,
                    error=f"worker crashed: {exc!s}",
                )
                raise
        finally:
            # Stop new thread-pool work before cancelling snapshot.
            self._stopping = True
            rl = self._restart_listener_task
            self._restart_listener_task = None
            if rl is not None and not rl.done():
                rl.cancel()
                with suppress(asyncio.CancelledError):
                    await rl
            snap = self._rolling_snapshot_task
            self._rolling_snapshot_task = None
            if snap is not None and not snap.done():
                snap.cancel()
                try:
                    await snap
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("rolling snapshot task shutdown failed", exc_info=True)
            self._blocking_executor_live = False
            try:
                self._blocking_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.debug("blocking thread pool shutdown failed", exc_info=True)
            await self._disconnect_redis()

    # _run_one_queue_item and _reschedule_if_needed are provided by InstanceWorkerTasksMixin
