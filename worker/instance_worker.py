from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np
import psutil
import redis.asyncio as aioredis

from account.switcher import AccountSwitcher
from actions.ad_skip import AdSkipper
from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_to_file
from config.loader import InstanceConfig, get_settings
from config.reference_naming import reference_file_basename, reference_png_abs_path
from fsm.machine import PlayerFSM
from fsm.states import InstanceState
from scheduler.claims import CooperativeClaims
from scheduler.queue import QueueItem, RedisQueue
from tasks.arena import ArenaTask
from tasks.base import BaseTask, TaskResult
from tasks.beast import BeastTask
from tasks.daily import DailyCheckinTask
from tasks.defend import DefendAllyTask
from tasks.gathering import GatheringTask
from tasks.main_city_check import MainCityCheckTask
from tasks.overlay_tap import OverlayTapTask
from tasks.page_detect import PageDetectTask
from tasks.training import TrainingTask

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, type] = {
    "arena": ArenaTask,
    "training": TrainingTask,
    "gathering": GatheringTask,
    "daily_checkin": DailyCheckinTask,
    "defend_ally": DefendAllyTask,
    "beast": BeastTask,
    "main_city_check": MainCityCheckTask,
    "page_detect": PageDetectTask,
}


class InstanceWorker:
    def __init__(self, instance_config: InstanceConfig) -> None:
        self._cfg = instance_config
        self._settings = get_settings()
        self._redis: aioredis.Redis | None = None  # type: ignore[type-arg]
        self._queue: RedisQueue | None = None
        self._claims: CooperativeClaims | None = None
        self._switcher: AccountSwitcher | None = None
        self._ad_skipper: AdSkipper | None = None
        self._bot_actions = BotActions()
        self._player_fsms: dict[str, PlayerFSM] = {}
        self._instance_state = InstanceState.READY
        self._ui_paused = False
        self._task_busy = asyncio.Event()
        self._rolling_snap_seq = 0

    async def _connect(self) -> None:
        self._redis = aioredis.from_url(self._settings.redis.url)
        self._queue = RedisQueue(self._redis)
        self._claims = CooperativeClaims(self._redis)
        self._switcher = AccountSwitcher(self._redis)
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
                "current_task_type": "",
                "current_task_id": "",
                "current_task_player": "",
                "current_task_started_at": "",
                "current_screen": "",
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
            await self._redis.hset(f"wos:instance:{self._cfg.instance_id}:state", mapping=mapping)
        except Exception:
            logger.debug("Failed to persist instance state to Redis", exc_info=True)

    def _worker_adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref if pref else DEFAULT_ADB_BIN

    async def _push_ui_screenshot(self, reference_name: str | None = None) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        (repo_root / "references").mkdir(parents=True, exist_ok=True)
        base = reference_file_basename(reference_name, self._cfg.instance_id)
        path = reference_png_abs_path(repo_root, base, self._cfg.instance_id)
        logger.debug(
            "[ui] %s: ADB screencap (serial=%s) → %s",
            self._cfg.instance_id,
            self._cfg.bluestacks_window_title,
            path,
        )
        ok, msg = adb_screencap_to_file(
            path,
            adb_bin=self._worker_adb_bin(),
            serial=self._cfg.bluestacks_window_title,
        )
        if ok:
            logger.debug("[ui] %s: saved screenshot %s", self._cfg.instance_id, path)
        else:
            logger.error("[ui] %s: screenshot failed: %s", self._cfg.instance_id, msg)
            raise RuntimeError(msg)

    async def _schedule_manual_task(self, player_id: str, task_type: str) -> None:
        task_id = f"ui:{player_id}:{task_type}:{uuid.uuid4().hex[:8]}"
        await self._queue.schedule(  # type: ignore[union-attr]
            task_id=task_id,
            player_id=player_id,
            task_type=task_type,
            priority=10_000,
            run_at=time.time(),
            instance_id=self._cfg.instance_id,
        )
        logger.info("Manual task queued: %s %s", task_type, player_id)

    async def _schedule_page_detect_if_unknown(self, *, reason: str) -> None:
        if self._redis is None or self._queue is None or not self._cfg.player_ids:
            return

        raw = await self._redis.hget(
            f"wos:instance:{self._cfg.instance_id}:state",
            "current_screen",
        )
        current_screen = (raw.decode() if isinstance(raw, bytes) else str(raw or "")).strip()
        if current_screen:
            return

        player_id = self._cfg.player_ids[0]
        queued = await self._queue.schedule(
            task_id=f"page_detect:{self._cfg.instance_id}:{reason}:{uuid.uuid4().hex[:8]}",
            player_id=player_id,
            task_type="page_detect",
            priority=90_000,
            run_at=time.time(),
            instance_id=self._cfg.instance_id,
            skip_if_duplicate=True,
        )
        if queued:
            logger.info(
                "Queued page_detect for %s (reason=%s)",
                self._cfg.instance_id,
                reason,
            )

    async def _handle_ui_command(self, raw: str | bytes) -> None:
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            data: dict[str, object] = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Invalid UI command JSON")
            return
        cmd = str(data.get("cmd", ""))
        inst_key = f"wos:instance:{self._cfg.instance_id}:state"
        match cmd:
            case "pause":
                self._ui_paused = True
                await self._redis.hset(inst_key, "paused", "1")  # type: ignore[union-attr]
                logger.info("UI pause enabled for %s", self._cfg.instance_id)
            case "resume":
                self._ui_paused = False
                await self._redis.hset(inst_key, "paused", "0")  # type: ignore[union-attr]
                logger.info("UI pause cleared for %s", self._cfg.instance_id)
            case "screenshot":
                try:
                    ref = data.get("name")
                    if ref is None:
                        ref = data.get("reference_name")
                    ref_s = str(ref).strip() if ref is not None else None
                    if ref_s == "":
                        ref_s = None
                    await self._push_ui_screenshot(reference_name=ref_s)
                except Exception:
                    logger.exception("UI screenshot failed")
            case "switch_player":
                pid = str(data.get("player_id", ""))
                if pid:
                    await self._switcher.switch_to(pid, self._cfg.instance_id)  # type: ignore[union-attr]
            case "run_task":
                pid = str(data.get("player_id", ""))
                ttype = str(data.get("task_type", ""))
                if pid and ttype:
                    await self._schedule_manual_task(pid, ttype)
            case "restart":
                await self._restart_instance()
            case _:
                logger.warning("Unknown UI command: %s", cmd)

    async def _drain_ui_commands(self) -> None:
        key = f"wos:ui:command:{self._cfg.instance_id}"
        while True:
            raw = await self._redis.rpop(key)  # type: ignore[union-attr]
            if raw is None:
                break
            await self._handle_ui_command(raw)

    async def _pop_next_task(self) -> QueueItem | None:
        return await self._queue.pop_due(self._cfg.instance_id)  # type: ignore[union-attr]

    async def _ensure_account(self, player_id: str) -> None:
        current = await self._switcher.current_player(self._cfg.instance_id)  # type: ignore[union-attr]
        if current != player_id:
            fsm = self._player_fsms.get(player_id)
            if fsm:
                fsm.switch_account()
            ok = await self._switcher.switch_to(player_id, self._cfg.instance_id)  # type: ignore[union-attr]
            if fsm:
                if ok:
                    fsm.switched()
                else:
                    fsm.recover()
            # Dismiss any entry popups / ads that appear after account switch
            await self._ad_skipper.handle_entry_screens()  # type: ignore[union-attr]

    def _build_task(self, item: QueueItem) -> BaseTask | None:
        if item.task_type == "overlay_tap":
            region = str(item.region or "").strip()
            if not region:
                logger.error("overlay_tap missing region on queue item %s", item.task_id)
                return None
            return OverlayTapTask(
                task_id=item.task_id,
                player_id=item.player_id,
                priority=item.priority,
                region_name=region,
                tap_x_pct=item.tap_x_pct,
                tap_y_pct=item.tap_y_pct,
                threshold=item.threshold,
                set_node=item.set_node,
                redis_client=self._redis,
            )
        factory = _TASK_REGISTRY.get(item.task_type)
        if factory is None:
            logger.error("Unknown task type: %s", item.task_type)
            return None
        return factory(  # type: ignore[return-value]
            task_id=item.task_id,
            player_id=item.player_id,
            priority=item.priority,
            redis_client=self._redis,
        )

    async def _execute_task(self, item: QueueItem, task: BaseTask) -> TaskResult | None:
        skip_fsm = getattr(task, "skip_fsm", False)

        fsm = self._player_fsms.get(item.player_id)
        if fsm and not skip_fsm:
            fsm.start_navigate()

        try:
            if task.is_cooperative:
                claimed = await self._claims.claim(  # type: ignore[union-attr]
                    task.task_type, item.player_id, ttl=300
                )
                if not claimed:
                    logger.info("Cooperative task %s already claimed, skipping", task.task_type)
                    return None

            if fsm and not skip_fsm:
                fsm.start_execute()

            result = await asyncio.wait_for(
                task.execute(self._cfg.instance_id),
                timeout=self._settings.worker.task_timeout_seconds,
            )

            if fsm and not skip_fsm:
                fsm.finish()

            return result

        except TimeoutError:
            logger.error("Task %s timed out on %s", item.task_id, self._cfg.instance_id)
            if fsm and not skip_fsm:
                fsm.recover()
            return None

        except Exception as exc:
            logger.exception("Task %s failed: %s", item.task_id, exc)
            if fsm and not skip_fsm:
                fsm.recover()
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

    async def _schedule_overlay_matches(self, overlay_results: dict[str, object]) -> None:
        """Enqueue ``overlay_tap`` for each matched overlay rule (deduped per region)."""
        if not self._cfg.player_ids:
            return

        active = await self._switcher.current_player(self._cfg.instance_id)  # type: ignore[union-attr]
        player_id = active if active else self._cfg.player_ids[0]
        now = time.time()
        is_main = bool(
            isinstance(overlay_results.get("main_city.visible"), dict)
            and overlay_results.get("main_city.visible", {}).get("matched")
        )
        for name, payload in overlay_results.items():
            if not isinstance(payload, dict):
                continue
            if not payload.get("matched"):
                continue
            if not payload.get("enqueue_tap", True):
                continue
            region = str(payload.get("region") or "").strip()
            if not region:
                continue
            # Safety: only click the new-chapter hint on main page.
            if name == "new_chapter.visible" and not is_main:
                continue
            task_id = f"ovl:{self._cfg.instance_id}:{name}:{uuid.uuid4().hex[:8]}"
            tap_x = payload.get("tap_x_pct")
            tap_y = payload.get("tap_y_pct")
            tap_x_pct = float(tap_x) if tap_x is not None else None
            tap_y_pct = float(tap_y) if tap_y is not None else None
            thr = payload.get("threshold")
            threshold = float(thr) if thr is not None else None
            sn = payload.get("set_node")
            set_node = str(sn).strip() if sn is not None and str(sn).strip() != "" else None
            pr = payload.get("priority")
            priority = int(pr) if pr is not None else 50_000
            queued = await self._queue.schedule(  # type: ignore[union-attr]
                task_id=task_id,
                player_id=player_id,
                task_type="overlay_tap",
                priority=priority,
                run_at=now,
                instance_id=self._cfg.instance_id,
                region=region,
                tap_x_pct=tap_x_pct,
                tap_y_pct=tap_y_pct,
                threshold=threshold,
                set_node=set_node,
                skip_if_duplicate=True,
            )
            if queued:
                logger.info(
                    "Device overlay matched %s → queued overlay_tap region=%s",
                    name,
                    region,
                )

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

            results = await run_overlay_analysis(
                image_bgr, repo_root=repo_root, current_screen=current_screen
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
        await self._schedule_page_detect_if_unknown(reason="startup")
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
                            f"wos:instance:{self._cfg.instance_id}:state",
                            "last_seen_at",
                            str(time.time()),
                        )
                    except Exception:
                        logger.debug("Failed to write last_seen_at heartbeat", exc_info=True)
                    last_heartbeat = now_m

            # Periodic health check
                if time.monotonic() - last_health_check >= health_interval:
                    alive = await self._health_check()
                    if not alive:
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

                task = self._build_task(item)
                if task is None:
                    continue

                skip_account = getattr(task, "skip_account_check", False)
                self._task_busy.set()

                state_key = f"wos:instance:{self._cfg.instance_id}:state"
                await self._set_instance_state(InstanceState.BUSY)
                await self._redis.hset(  # type: ignore[union-attr]
                    state_key,
                    mapping={
                        "current_task_type": item.task_type,
                        "current_task_id": item.task_id,
                        "current_task_player": item.player_id,
                        "current_task_started_at": str(time.time()),
                        "current_task_region": item.region or "",
                        "current_task_threshold": (
                            "" if item.threshold is None else str(item.threshold)
                        ),
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
                    if result is not None and result.next_run_at is not None:
                        import time as stdlib_time

                        run_at = stdlib_time.mktime(result.next_run_at.timetuple())
                        await self._queue.schedule(  # type: ignore[union-attr]
                            task_id=item.task_id,
                            player_id=item.player_id,
                            task_type=item.task_type,
                            priority=item.priority,
                            run_at=run_at,
                            instance_id=self._cfg.instance_id,
                            region=item.region,
                        )
                    if result is not None:
                        logger.info(
                            "Task done %s: id=%s success=%s next_run_at=%s",
                            self._cfg.instance_id,
                            item.task_id,
                            getattr(result, "success", None),
                            getattr(result, "next_run_at", None),
                        )
                    else:
                        logger.info(
                            "Task done %s: id=%s (no result)",
                            self._cfg.instance_id,
                            item.task_id,
                        )
                except Exception as exc:
                    await self._set_instance_state(
                        InstanceState.CRASHED, error=f"unhandled task failure: {exc!s}"
                    )
                    await self._handle_failure(item, exc)
                finally:
                    self._task_busy.clear()
                    await self._set_instance_state(InstanceState.READY)
                    await self._redis.hset(  # type: ignore[union-attr]
                        state_key,
                        mapping={
                            "current_task_type": "",
                            "current_task_id": "",
                            "current_task_player": "",
                            "current_task_started_at": "",
                            "current_task_region": "",
                            "current_task_threshold": "",
                        },
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._set_instance_state(InstanceState.CRASHED, error=f"worker crashed: {exc!s}")
            raise
