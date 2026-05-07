from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np
import psutil
import redis.asyncio as aioredis
from actions.ad_skip import AdSkipper
from actions.tap import BotActions
from analysis.overlay import parse_duration_seconds, run_overlay_analysis
from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_to_file
from config.loader import InstanceConfig, get_settings
from config.reference_naming import reference_file_basename, reference_png_abs_path
from fsm.machine import PlayerFSM
from fsm.states import InstanceState
from scheduler.claims import CooperativeClaims
from scheduler.queue import QueueItem, RedisQueue
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask
from worker.instance_worker_overlay import InstanceWorkerOverlayMixin
from worker.instance_worker_tasks import InstanceWorkerTasksMixin
from worker.instance_worker_ui import InstanceWorkerUiMixin

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, type] = {}

# Redis hash for UI/monitoring.
_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


class InstanceWorker(InstanceWorkerUiMixin, InstanceWorkerOverlayMixin, InstanceWorkerTasksMixin):
    def __init__(self, instance_config: InstanceConfig) -> None:
        self._cfg = instance_config
        self._settings = get_settings()
        self._redis: aioredis.Redis | None = None  # type: ignore[type-arg]
        self._queue: RedisQueue | None = None
        self._claims: CooperativeClaims | None = None
        self._ad_skipper: AdSkipper | None = None
        self._bot_actions = BotActions()
        self._player_fsms: dict[str, PlayerFSM] = {}
        self._instance_state = InstanceState.READY
        self._ui_paused = False
        self._task_busy = asyncio.Event()
        self._rolling_snap_seq = 0
        self._last_current_screen: str | None = None
        self._overlay_rule_eval_state: dict[str, float] = {}

    # Legacy hook removed: mail gift check will be a DSL scenario when needed.

    async def _connect(self) -> None:
        self._redis = aioredis.from_url(self._settings.redis.url)
        self._queue = RedisQueue(self._redis)
        self._claims = CooperativeClaims(self._redis)
        self._ad_skipper = AdSkipper(self._cfg.instance_id)

        loop = asyncio.get_running_loop()
        for player_id in self._cfg.player_ids:
            fsm = PlayerFSM(player_id, self._redis, loop=loop)
            await fsm.restore_from_redis()
            self._player_fsms[player_id] = fsm

        inst_key = f"wos:instance:{self._cfg.instance_id}:state"
        await self._redis.hset(
            inst_key,
            mapping={
                "state": InstanceState.READY,
                "active_player": "",
                "paused": "0",
                "worker_started_at": str(time.time()),
                "last_seen_at": str(time.time()),
                "last_error": "",
                "current_task_player": "",
                "current_task_started_at": "",
                "current_task_region": "",
                "current_screen": "",
                "current_scenario": "",
            },
        )

    async def _set_instance_state(self, state: InstanceState, *, error: str = "") -> None:
        """Persist instance state to Redis for UI/debugging."""
        self._instance_state = state
        if self._redis is None:
            return
        mapping: dict[str, str] = {"state": str(state)}
        if error:
            mapping["last_error"] = error[:500]
        else:
            # Clear stale error when state changes successfully.
            mapping["last_error"] = ""
        try:
            await self._redis.hset(
                _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                mapping=mapping,
            )
        except Exception:
            logger.debug("Failed to persist instance state to Redis", exc_info=True)

    def _worker_adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref if pref else DEFAULT_ADB_BIN

    async def _pop_next_task(self) -> QueueItem | None:
        current_screen = ""
        if self._redis is not None:
            raw = await self._redis.hget(f"wos:instance:{self._cfg.instance_id}:state", "current_screen")
            if raw is not None:
                current_screen = raw.decode() if isinstance(raw, bytes) else str(raw)
                current_screen = current_screen.strip()
        return await self._queue.pop_due(  # type: ignore[union-attr]
            self._cfg.instance_id,
            current_screen=current_screen,
        )

    async def _resolve_queue_item_player(self, item: QueueItem) -> QueueItem:
        """Resolve device-level queue items (player_id="") to an actual player id."""
        if item.player_id:
            return item
        active = None
        if self._redis is not None:
            raw = await self._redis.hget(
                f"wos:instance:{self._cfg.instance_id}:state", "active_player"
            )
            if raw:
                active = raw.decode() if isinstance(raw, bytes) else str(raw)
        resolved = (active or (self._cfg.player_ids[0] if self._cfg.player_ids else "")).strip()
        if not resolved:
            return item
        return QueueItem(
            task_id=item.task_id,
            player_id=resolved,
            task_type=item.task_type,
            priority=item.priority,
            run_at=item.run_at,
            instance_id=item.instance_id,
            region=item.region,
            tap_x_pct=item.tap_x_pct,
            tap_y_pct=item.tap_y_pct,
            threshold=item.threshold,
            score=item.score,
            set_node=item.set_node,
            dsl_scenario=item.dsl_scenario,
        )

    async def _ensure_account(self, player_id: str) -> None:
        # Account switching is not implemented in this codebase. We only persist
        # the "active_player" label for UI/overlay routing.
        if self._redis is not None:
            await self._redis.hset(
                f"wos:instance:{self._cfg.instance_id}:state",
                "active_player",
                player_id,
            )
        # Best-effort dismiss entry popups / ads.
        await self._ad_skipper.handle_entry_screens()  # type: ignore[union-attr]

    def _build_task(self, item: QueueItem) -> BaseTask | None:
        factory = _TASK_REGISTRY.get(item.task_type)
        if factory is None:
            # Default: treat unknown task_type as a DSL scenario key.
            return DslScenarioTask(
                task_id=item.task_id,
                player_id=item.player_id,
                priority=item.priority,
                scenario_key=item.task_type,
                redis_client=self._redis,
            )
        return factory(  # type: ignore[return-value]
            task_id=item.task_id,
            player_id=item.player_id,
            priority=item.priority,
            redis_client=self._redis,
        )

    async def _execute_task(self, item: QueueItem, task: BaseTask) -> TaskResult | None:
        skip_fsm = getattr(task, "skip_fsm", False)

        fsm = self._player_fsms.get(item.player_id)

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

    def _ensure_whiteout_at_worker_start(self) -> None:
        BotActions().ensure_game_foreground(self._cfg.instance_id)

    async def _handle_failure(self, item: QueueItem, error: Exception) -> None:
        logger.error("Unhandled failure for task %s: %s", item.task_id, error)

    async def _health_check(self) -> bool:
        try:
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = proc.info["name"] or ""
                    if "bluestacks" in name.lower():
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except PermissionError:
            # Some macOS environments deny sysctl-based PID enumeration.
            # Treat as "unknown but OK" to avoid a restart loop.
            logger.debug("Health check skipped (no permission to enumerate processes)")
            return True
        return False

    async def _restart_instance(self) -> None:
        logger.warning("Restarting BlueStacks instance %s", self._cfg.instance_id)
        await self._set_instance_state(InstanceState.RESTARTING)
        await self._redis.delete(f"wos:instance:{self._cfg.instance_id}:lock")

        # Restart must not depend on OCR availability (OCR is optional/remote).
        try:
            self._bot_actions.restart_application(self._cfg.instance_id)
            await asyncio.sleep(3.0)
            await asyncio.to_thread(self._bot_actions.ensure_game_foreground, self._cfg.instance_id)
        except Exception:
            logger.exception("Failed to restart application on %s", self._cfg.instance_id)
            await self._set_instance_state(
                InstanceState.CRASHED, error="restart_application failed (see logs)"
            )
            return

        await self._set_instance_state(InstanceState.READY)
        # Dismiss entry screens that appear after game restart (best-effort).
        try:
            await self._ad_skipper.handle_entry_screens()  # type: ignore[union-attr]
        except Exception:
            logger.debug("Ad-skip after restart failed", exc_info=True)

    def _grab_layout_bgr(self) -> np.ndarray:
        return self._bot_actions.capture_screen_bgr(self._cfg.instance_id)

    async def _overlay_analyze_bgr(self, image_bgr: np.ndarray) -> None:
        """Run ``references/analyze.yaml`` overlay rules on an ADB frame (BGR)."""
        repo_root = Path(__file__).resolve().parent.parent
        try:
            # Read current_screen written by Navigator after successful navigation.
            current_screen: str | None = None
            if self._redis is not None:
                raw = await self._redis.hget(
                    f"wos:instance:{self._cfg.instance_id}:state", "current_screen"
                )
                if raw:
                    current_screen = raw.decode() if isinstance(raw, bytes) else str(raw)
                    current_screen = current_screen.strip() or None

            # One-shot hooks on screen transitions (currently none).
            self._last_current_screen = current_screen

            results = await run_overlay_analysis(
                image_bgr,
                repo_root=repo_root,
                current_screen=current_screen,
                rule_eval_state=self._overlay_rule_eval_state,
            )
        except Exception:
            logger.exception("overlay analyze failed on %s", self._cfg.instance_id)
            return
        await self._schedule_overlay_matches(results)

    async def _device_reference_snapshot_tick(self) -> None:
        """ADB screencap → rolling preview PNG + overlay rules (same frame)."""
        repo_root = Path(__file__).resolve().parent.parent
        (repo_root / "references").mkdir(parents=True, exist_ok=True)
        base = reference_file_basename(None, self._cfg.instance_id)
        path = reference_png_abs_path(repo_root, base, self._cfg.instance_id)

        logger.debug(
            "[rolling] %s: ADB screencap (serial=%s) → %s",
            self._cfg.instance_id,
            self._cfg.bluestacks_window_title,
            path,
        )

        try:
            image_bgr = await asyncio.to_thread(self._grab_layout_bgr)
        except Exception:
            logger.exception(
                "[rolling] %s: screenshot failed (exception during capture)",
                self._cfg.instance_id,
            )
            return

        def _write_png_atomic(p: Path, img: np.ndarray) -> bool:
            """Write to a temp file in the same dir, then ``os.replace`` (atomic on macOS/Linux)."""
            import cv2

            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".rolling-", suffix=".png", dir=p.parent)
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                if not cv2.imwrite(str(tmp), img):
                    tmp.unlink(missing_ok=True)
                    return False
                os.replace(tmp, p)
                return True
            except OSError:
                tmp.unlink(missing_ok=True)
                raise

        if not await asyncio.to_thread(_write_png_atomic, path, image_bgr):
            logger.warning("[rolling] %s: PNG write failed %s", self._cfg.instance_id, path)
            return

        self._rolling_snap_seq += 1
        h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
        logger.debug(
            "[rolling] %s: saved screenshot %s (%d×%d), tick #%d",
            self._cfg.instance_id,
            path,
            w,
            h,
            self._rolling_snap_seq,
        )

        cfg = self._settings.worker
        overlay_skipped_busy = not cfg.overlay_analyze_when_busy and self._task_busy.is_set()
        if overlay_skipped_busy:
            logger.debug(
                "overlay-after-snapshot skipped (task busy, overlay_analyze_when_busy=false)"
            )
            return
        await self._overlay_analyze_bgr(image_bgr)

    async def _device_reference_snapshot_loop(self) -> None:
        cfg = self._settings.worker
        await asyncio.sleep(0.5)
        logger.info(
            "[rolling] %s: snapshot loop started (interval=%.2fs)",
            self._cfg.instance_id,
            float(cfg.device_reference_snapshot_interval_seconds),
        )
        while True:
            try:
                interval = cfg.device_reference_snapshot_interval_seconds
                await asyncio.sleep(max(0.3, float(interval)))
                if self._ui_paused:
                    continue
                await self._device_reference_snapshot_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "device_reference_snapshot_loop error on %s", self._cfg.instance_id
                )

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
            await asyncio.to_thread(self._ensure_whiteout_at_worker_start)
        except Exception:
            logger.exception(
                "Whiteout foreground check/launch failed for instance %s", self._cfg.instance_id
            )
        # Legacy: page detect disabled (YAML-only mode).
        asyncio.create_task(
            self._device_reference_snapshot_loop(),
            name=f"refsnap-{self._cfg.instance_id}",
        )
        health_interval = self._settings.worker.health_check_interval_seconds
        last_health_check = time.monotonic()
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

                # Periodic health check
                if time.monotonic() - last_health_check >= health_interval:
                    if not await self._health_check():
                        await self._restart_instance()
                    last_health_check = time.monotonic()

                await self._drain_ui_commands()
                while self._ui_paused:
                    await self._drain_ui_commands()
                    await asyncio.sleep(0.3)

                item = await self._pop_next_task()
                if item is None:
                    await asyncio.sleep(2.0)
                    continue
                item = await self._resolve_queue_item_player(item)

                task = self._build_task(item)
                if task is None:
                    continue

                await self._run_one_queue_item(item, task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._set_instance_state(InstanceState.CRASHED, error=f"worker crashed: {exc!s}")
            raise

    # _run_one_queue_item and _reschedule_if_needed are provided by InstanceWorkerTasksMixin
