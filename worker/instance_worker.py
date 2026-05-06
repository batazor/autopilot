from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

import psutil
import redis.asyncio as aioredis

from account.switcher import AccountSwitcher
from actions.ad_skip import AdSkipper
from actions.tap import BotActions
from actions.recovery import RecoveryHandler
from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_to_file
from capture.window import QuartzCapture
from config.loader import InstanceConfig, get_settings
from config.reference_naming import reference_file_basename, reference_png_abs_path
from fsm.machine import PlayerFSM
from fsm.states import InstanceState, PlayerState
from scheduler.claims import CooperativeClaims
from scheduler.queue import QueueItem, RedisQueue
from tasks.arena import ArenaTask
from tasks.base import BaseTask, TaskResult
from tasks.beast import BeastTask
from tasks.daily import DailyCheckinTask
from tasks.defend import DefendAllyTask
from tasks.gathering import GatheringTask
from tasks.training import TrainingTask
from .redis_log_handler import RedisAsyncLogHandler

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, type] = {
    "arena": ArenaTask,
    "training": TrainingTask,
    "gathering": GatheringTask,
    "daily_checkin": DailyCheckinTask,
    "defend_ally": DefendAllyTask,
    "beast": BeastTask,
}


class InstanceWorker:
    def __init__(self, instance_config: InstanceConfig) -> None:
        self._cfg = instance_config
        self._settings = get_settings()
        self._redis: aioredis.Redis | None = None  # type: ignore[type-arg]
        self._queue: RedisQueue | None = None
        self._claims: CooperativeClaims | None = None
        self._switcher: AccountSwitcher | None = None
        self._recovery: RecoveryHandler | None = None
        self._ad_skipper: AdSkipper | None = None
        self._capture = QuartzCapture()
        self._player_fsms: dict[str, PlayerFSM] = {}
        self._instance_state = InstanceState.READY
        self._ui_paused = False

    async def _connect(self) -> None:
        self._redis = aioredis.from_url(self._settings.redis.url)
        self._queue = RedisQueue(self._redis)
        self._claims = CooperativeClaims(self._redis)
        self._switcher = AccountSwitcher(self._redis)
        self._recovery = RecoveryHandler()
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
                "current_task_type": "",
                "current_task_id": "",
                "current_task_player": "",
                "current_task_started_at": "",
            },
        )

        log_handler = RedisAsyncLogHandler(self._redis, self._cfg.instance_id)
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(log_handler)

    async def _push_ui_screenshot(self, reference_name: str | None = None) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        (repo_root / "references").mkdir(parents=True, exist_ok=True)
        base = reference_file_basename(reference_name, self._cfg.instance_id)
        path = reference_png_abs_path(repo_root, base, self._cfg.instance_id)
        ok, msg = adb_screencap_to_file(
            path,
            adb_bin=DEFAULT_ADB_BIN,
            serial=self._cfg.bluestacks_window_title,
        )
        if ok:
            logger.info("Reference screenshot (ADB) saved to %s", path)
        else:
            logger.error("ADB screenshot failed: %s", msg)
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
            case "recovery":
                await self._recovery.recover_to_main(self._cfg.instance_id)  # type: ignore[union-attr]
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
        factory = _TASK_REGISTRY.get(item.task_type)
        if factory is None:
            logger.error("Unknown task type: %s", item.task_type)
            return None
        return factory(  # type: ignore[return-value]
            task_id=item.task_id,
            player_id=item.player_id,
            priority=item.priority,
        )

    async def _execute_task(self, item: QueueItem) -> TaskResult | None:
        task = self._build_task(item)
        if task is None:
            return None

        fsm = self._player_fsms.get(item.player_id)
        if fsm:
            fsm.start_navigate()

        try:
            if task.is_cooperative:
                claimed = await self._claims.claim(  # type: ignore[union-attr]
                    task.task_type, item.player_id, ttl=300
                )
                if not claimed:
                    logger.info("Cooperative task %s already claimed, skipping", task.task_type)
                    return None

            if fsm:
                fsm.start_execute()

            result = await asyncio.wait_for(
                task.execute(self._cfg.instance_id),
                timeout=self._settings.worker.task_timeout_seconds,
            )

            if fsm:
                fsm.finish()

            return result

        except TimeoutError:
            logger.error("Task %s timed out on %s", item.task_id, self._cfg.instance_id)
            if fsm:
                fsm.recover()
            return None

        except Exception as exc:
            logger.exception("Task %s failed: %s", item.task_id, exc)
            if fsm:
                fsm.recover()
            return None

        finally:
            if task.is_cooperative:
                await self._claims.release(task.task_type, item.player_id)  # type: ignore[union-attr]

    async def _handle_failure(self, item: QueueItem, error: Exception) -> None:
        logger.error("Unhandled failure for task %s: %s", item.task_id, error)
        ok = await self._recovery.recover_to_main(self._cfg.instance_id)  # type: ignore[union-attr]
        if not ok:
            await self._restart_instance()

    async def _health_check(self) -> bool:
        title = self._cfg.bluestacks_window_title
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = proc.info["name"] or ""
                if "bluestacks" in name.lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    async def _restart_instance(self) -> None:
        logger.warning("Restarting BlueStacks instance %s", self._cfg.instance_id)
        self._instance_state = InstanceState.RESTARTING
        await self._redis.hset(  # type: ignore[union-attr]
            f"wos:instance:{self._cfg.instance_id}:state",
            "state",
            InstanceState.RESTARTING,
        )
        await self._redis.delete(f"wos:instance:{self._cfg.instance_id}:lock")

        ok = await self._recovery.restart_game(self._cfg.instance_id)  # type: ignore[union-attr]
        if ok:
            self._instance_state = InstanceState.READY
            await self._redis.hset(  # type: ignore[union-attr]
                f"wos:instance:{self._cfg.instance_id}:state",
                "state",
                InstanceState.READY,
            )
            # Dismiss entry screens that appear after game restart
            await self._ad_skipper.handle_entry_screens()  # type: ignore[union-attr]
        else:
            logger.critical("Failed to restart instance %s", self._cfg.instance_id)

    async def run(self) -> None:
        await self._connect()
        logger.info("Worker started for instance %s", self._cfg.instance_id)
        health_interval = self._settings.worker.health_check_interval_seconds
        last_health_check = time.monotonic()

        while True:
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

            state_key = f"wos:instance:{self._cfg.instance_id}:state"
            await self._redis.hset(  # type: ignore[union-attr]
                state_key,
                mapping={
                    "current_task_type": item.task_type,
                    "current_task_id": item.task_id,
                    "current_task_player": item.player_id,
                    "current_task_started_at": str(time.time()),
                },
            )
            try:
                await self._ensure_account(item.player_id)
                result = await self._execute_task(item)
                await self._drain_ui_commands()
                if result and result.next_run_at:
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
            except Exception as exc:
                await self._handle_failure(item, exc)
            finally:
                await self._redis.hset(  # type: ignore[union-attr]
                    state_key,
                    mapping={
                        "current_task_type": "",
                        "current_task_id": "",
                        "current_task_player": "",
                        "current_task_started_at": "",
                    },
                )
