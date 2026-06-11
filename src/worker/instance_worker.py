from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from adb import BotActions, abort_pending_approval, click_approval_enabled
from adb.screencap import DEFAULT_ADB_BIN
from config.capture_rate import capture_interval_s_for_scenario_key

# Both functions are imported solely so they live as attributes on the
# ``worker.instance_worker`` module — ``worker.instance_worker_redis._connect``
# resolves them via ``getattr(instance_worker, ...)`` (primary +
# fallback), and the identity-probe / resolve-queue-item-player tests
# monkeypatch them through the same module. Direct usage in this file is
# absent, so the F401 silencing keeps a linter sweep from re-removing them.
from config.devices import (  # noqa: F401 — re-exported for redis/test monkeypatch
    player_ids_for_device,
    player_ids_for_device_candidates,
)
from config.paths import repo_root
from config.reference_naming import (
    rolling_preview_basename,
    temporal_png_abs_path,
)
from navigation.detector import ScreenDetector
from navigation.lifecycle_states import InstanceState
from popup import PopupDetector
from tasks.base import BaseTask, TaskResult
from tasks.dsl_scenario import DslScenarioTask
from tasks.radar_scan import TASK_TYPE as RADAR_SCAN_TASK_TYPE
from tasks.radar_scan import RadarScanTask
from worker.instance_worker_blocking import InstanceWorkerBlockingMixin
from worker.instance_worker_health import InstanceWorkerHealthMixin
from worker.instance_worker_overlay import InstanceWorkerOverlayMixin
from worker.instance_worker_redis import InstanceWorkerRedisMixin
from worker.instance_worker_rolling import InstanceWorkerRollingMixin
from worker.instance_worker_screen import (
    InstanceWorkerScreenDetectMixin,
    InstanceWorkerScreenMixin,
)
from worker.instance_worker_tasks import (
    InstanceWorkerTasksMixin,
    _history_key_for_instance,
    _running_key_for_instance,
)
from worker.instance_worker_ui import InstanceWorkerUiMixin

if TYPE_CHECKING:
    from pathlib import Path

    import redis.asyncio as aioredis

    from config.loader import InstanceConfig, Settings
    from ocr.client import OcrClient
    from scheduler.queue import QueueItem, RedisQueue

logger = logging.getLogger(__name__)

_TASK_REGISTRY: dict[str, type] = {
    RADAR_SCAN_TASK_TYPE: RadarScanTask,
}

# Redis hash for UI/monitoring.
_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


def _is_adb_offline_error(exc: BaseException) -> bool:
    """``AdbController`` raises ``RuntimeError`` with this shape on offline serials."""
    if not isinstance(exc, RuntimeError):
        return False
    s = str(exc)
    return (
        ("not found or not in 'device' state" in s)
        or ("device '" in s and "' not found" in s)
        or ("device not found" in s)
        or ("no devices/emulators found" in s)
    )

# DSL scenarios pushed once per instance start. Each entry must be a key resolvable
# by `DslScenarioTask` (i.e. a YAML file under `scenarios/**/{key}.yaml`).
#
# ``who_i_am`` is enqueued separately by
# ``_maybe_enqueue_who_i_am_when_active_player_missing`` (and only when
# ``active_player`` is missing). ``check_main_city`` is seeded unconditionally
# at low priority so the bot always has a navigation goal after restart — when
# ``active_player`` is *already* set in Redis from the prior session,
# ``who_i_am`` is skipped and nothing else would route us out of an
# intermediate popup-bearing screen until the 5-min cron tick. The seed is
# device_level (see ``check_main_city.yaml``) so it works without a resolved
# ``active_player``; priority 10 keeps it below ads / overlays / identity.
_STARTUP_SEED_TASKS: tuple[tuple[str, int], ...] = (
    ("check_main_city", 10),
)


def _startup_stale_boot_task_types(root: Path) -> tuple[str, ...]:
    """Queue types cleared on worker boot before fresh seeds are published."""
    del root  # signature kept for callers; no module-discovery needed
    return ("who_i_am", "check_main_city")
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

    def __init__(
        self,
        instance_config: InstanceConfig,
        settings: Settings,
        bot_actions: BotActions,
        ocr_client: OcrClient,
        *,
        redis: aioredis.Redis | None = None,  # type: ignore[type-arg]
        queue: RedisQueue | None = None,
    ) -> None:
        self._cfg = instance_config
        self._settings = settings
        self._redis = redis
        self._queue = queue
        self._owns_redis = redis is None
        self._claims: Any | None = None
        self._bot_actions = bot_actions
        self._instance_state = InstanceState.READY
        self._ui_paused = False
        self._startup_pause_reason = ""
        self._task_busy = asyncio.Event()
        self._rolling_snap_seq = 0
        self._last_current_screen: str | None = None
        self._last_detected_screen: str | None = None
        self._last_detected_screen_at: float = 0.0
        # Monotonic clock at which ``current_screen`` was *hard-cleared* to
        # None (i.e. dropped past the sticky "soft unknown" window). 0.0 means
        # "not currently unknown". Used by the popup-dismiss fallback so it
        # waits the full 10s of confirmed-unknown before firing.
        self._unknown_since: float = 0.0
        self._screen_unknown_streak = 0
        self._ocr_client = ocr_client
        self._screen_detector = ScreenDetector(ocr_client)
        # Template-free pop-up detector, with screen analysis to avoid treating
        # known event/ad pages as generic pop-ups.
        self._popup_detector = PopupDetector(ocr_client)
        # Monotonic clock of the last detector-issued pop-up tap; paired with a
        # Redis cooldown lock so we don't re-tap the same modal every frame.
        self._last_popup_tap_mono: float = 0.0
        # Per-player TTL state for overlay rules. Outer key = active player id
        # at evaluation time (``""`` for device-level / pre-identity ticks);
        # inner = rule logical name → ``time.monotonic()`` of last eval.
        # Switching ``active_player`` swaps which sub-dict is mutated, so two
        # accounts on the same emulator don't share cooldowns (e.g. a 5m red-
        # dot throttle on player A doesn't suppress overlays on player B).
        self._overlay_rule_eval_state_by_player: dict[str, dict[str, float]] = {}
        self._overlay_ttl_rev_by_player: dict[str, str] = {}
        self._overlay_ttl_last_sync_mono_by_player: dict[str, float] = {}
        self._overlay_ttl_last_persist_mono_by_player: dict[str, float] = {}
        # Avoid asyncio default executor shutdown races during app stop/reload.
        self._blocking_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix=f"wos-{self._cfg.instance_id}-",
        )
        # Rolling ADB preview must keep updating while long DSL tasks hold the
        # main blocking pool (navigation, OCR, overlay on the same 4 threads).
        self._rolling_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix=f"wos-{self._cfg.instance_id}-rolling-",
        )
        self._rolling_snapshot_task: asyncio.Task[None] | None = None
        self._rolling_analyze_task: asyncio.Task[None] | None = None
        self._startup_overlay_task: asyncio.Task[None] | None = None
        self._abort_task_listener_task: asyncio.Task[None] | None = None
        self._blocking_executor_live: bool = True
        self._stopping: bool = False
        self._task_registry = _TASK_REGISTRY
        # Handle of the currently running ``task.execute()`` coroutine, if any.
        # Set inside ``_execute_task`` so external triggers (watchdog restart)
        # can cancel it instead of letting the task
        # tap on a force-stopped game. ``_task_aborted_for_restart`` is the
        # flag that distinguishes "we cancelled this for a restart" (translate
        # to a failed TaskResult) from a worker-shutdown cascade (propagate).
        self._current_task_handle: asyncio.Task[Any] | None = None
        # Per-module rolling-capture override (seconds), set while a scenario
        # from a module declaring ``capture_interval_ms`` is executing; read by
        # the rolling loop. None = use the global snapshot interval.
        self._capture_interval_override_s: float | None = None
        self._task_aborted_for_restart: bool = False
        self._worker_boot_at: float = 0.0
        # First screen detect that is not ``loading`` / empty — boot grace starts here.
        self._boot_interactive_at: float = 0.0
        self._task_abort_result_reason: str = "aborted_for_restart"
        self._task_abort_reschedule: bool = False

    # Legacy hook removed: mail gift check will be a DSL scenario when needed.

    def _worker_adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref or DEFAULT_ADB_BIN

    def _build_task(self, item: QueueItem) -> BaseTask | None:
        factory = _TASK_REGISTRY.get(item.task_type)
        if factory is None:
            # ``optimizer.dispatcher.build_envelope`` sets ``task_type="dsl_scenario"``
            # as a marker and carries the real key in ``dsl_scenario`` (e.g.
            # ``level_up_bahiti``). Overlay / cron paths put the key directly
            # in ``task_type``. Prefer the explicit field when set so the
            # optimizer's "Queue for bot" button doesn't queue a task that
            # immediately fails with ``scenario_not_found: dsl_scenario``.
            scenario_key = (item.dsl_scenario or "").strip() or item.task_type
            return DslScenarioTask(
                task_id=item.task_id,
                player_id=item.player_id,
                priority=item.priority,
                scenario_key=scenario_key,
                tap_region=item.region or "",
                tap_x_pct=item.tap_x_pct,
                tap_y_pct=item.tap_y_pct,
                start_step_index=item.start_step_index,
                args=item.args or {},
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
        inner: asyncio.Task[TaskResult] | None = None
        try:
            if task.is_cooperative:
                claimed = await self._claims.claim(  # type: ignore[union-attr]
                    task.task_type, item.player_id, ttl=300
                )
                if not claimed:
                    logger.info("Cooperative task %s already claimed, skipping", task.task_type)
                    return None

            # Wrap so an external trigger (watchdog/FSM restart) can cancel
            # this exact coroutine via ``_cancel_current_task`` — otherwise
            # an in-flight scenario keeps tapping a force-stopped game.
            inner = asyncio.create_task(task.execute(self._cfg.instance_id))
            self._current_task_handle = inner
            # Speed up the rolling capture loop if this scenario's module asks
            # for a faster frame rate (module.yaml ``capture_interval_ms``).
            self._capture_interval_override_s = capture_interval_s_for_scenario_key(
                repo_root(), getattr(task, "scenario_key", "")
            )
            # In approval mode the task is legitimately blocked on operator
            # input (``_require_approval`` busy-waits on Redis); the worker
            # timeout would kill it mid-wait and discard the pending tap.
            approval_on = click_approval_enabled(self._cfg.instance_id)
            try:
                if approval_on:
                    result = await inner
                else:
                    result = await asyncio.wait_for(
                        inner,
                        timeout=self._settings.worker.task_timeout_seconds,
                    )
            finally:
                if self._current_task_handle is inner:
                    self._current_task_handle = None
                    self._capture_interval_override_s = None

            return result

        except TimeoutError:
            logger.exception("Task %s timed out on %s", item.task_id, self._cfg.instance_id)
            return None

        except asyncio.CancelledError:
            # Our cancel (restart) vs. a worker-shutdown cascade. Only the
            # former should be swallowed and reported as a failed task; the
            # latter must propagate so ``run()`` can shut down cleanly.
            if self._task_aborted_for_restart:
                self._task_aborted_for_restart = False
                result_reason = self._task_abort_result_reason or "aborted_for_restart"
                reschedule = bool(self._task_abort_reschedule)
                self._task_abort_result_reason = "aborted_for_restart"
                self._task_abort_reschedule = False
                logger.warning(
                    "Task %s aborted: %s (%s)",
                    item.task_id,
                    result_reason,
                    self._cfg.instance_id,
                )
                metadata: dict[str, object] = {"reason": result_reason}
                if result_reason.startswith("preempted_by"):
                    metadata["preempted"] = True
                if reschedule:
                    metadata["resume_from_step_index"] = int(item.start_step_index or 0)
                    if self._redis is not None:
                        with suppress(Exception):
                            raw_step = await self._redis.hget(
                                _INST_STATE_KEY_FMT.format(
                                    instance_id=self._cfg.instance_id
                                ),
                                "last_active_scenario_step",
                            )
                            step_s = (
                                raw_step.decode()
                                if isinstance(raw_step, (bytes, bytearray))
                                else str(raw_step or "")
                            ).strip()
                            metadata["resume_from_step_index"] = max(
                                0, int(step_s or "0")
                            )
                return TaskResult(
                    success=False,
                    next_run_at=datetime.now(tz=UTC) if reschedule else None,
                    metadata=metadata,
                )
            raise

        except Exception as exc:
            # Mid-task ADB disconnect (BlueStacks killed, USB unplug, …) raises
            # ``RuntimeError`` from ``AdbController._verify_available``. Treat it
            # the same as a startup-offline detection: self-pause + clean info
            # log, no traceback. The watchdog will auto-resume when the device
            # is back, and the seeded/queued task pops fresh.
            if _is_adb_offline_error(exc):
                logger.info(
                    "Task %s: device offline mid-run — self-pausing (%s)",
                    item.task_id,
                    self._cfg.instance_id,
                )
                self._ui_paused = True
                if self._redis is not None:
                    with suppress(Exception):
                        await self._redis.hset(  # type: ignore[union-attr]
                            _INST_STATE_KEY_FMT.format(
                                instance_id=self._cfg.instance_id
                            ),
                            mapping={
                                "paused": "1",
                                "auto_paused": "1",
                                "last_error": "device offline (ADB)",
                            },
                        )
                return None
            logger.exception("Task %s failed", item.task_id)
            return None

        finally:
            if task.is_cooperative:
                await self._claims.release(task.task_type, item.player_id)  # type: ignore[union-attr]

    async def _cancel_current_task(
        self,
        reason: str,
        *,
        result_reason: str = "aborted_for_restart",
        reschedule: bool = False,
    ) -> bool:
        """Cancel the in-flight ``task.execute()`` so a restart doesn't tap a dead app.

        Returns True if a cancel was actually issued. Sets
        ``_task_aborted_for_restart`` so ``_execute_task`` translates the
        resulting ``CancelledError`` into a failed ``TaskResult`` (rather than
        propagating as a worker shutdown).
        """
        handle = self._current_task_handle
        if handle is None or handle.done():
            return False
        logger.warning(
            "Aborting current task on %s: %s",
            self._cfg.instance_id,
            reason,
        )
        # If the task is blocked inside ``_require_approval`` (tap/swipe awaiting
        # operator approval), it runs on an ``asyncio.to_thread`` worker thread
        # that ``handle.cancel()`` cannot interrupt — the coroutine raises
        # ``CancelledError`` but the thread keeps polling Redis and would tap the
        # restarted game if the operator later approves. Stamp the abort signal
        # first so that busy-wait gives up and clears the approvals slot too.
        with suppress(Exception):
            await self._run_blocking(
                abort_pending_approval, self._cfg.instance_id, result_reason
            )
        self._task_aborted_for_restart = True
        self._task_abort_result_reason = result_reason
        self._task_abort_reschedule = bool(reschedule)
        handle.cancel()
        return True

    async def _run_abort_task_listener(self) -> None:
        """Cross-process abort: watchdog publishes here right before force-stop.

        Channel: ``wos:events:abort_task:<instance_id>`` — pubsub so the signal
        arrives even while the worker is mid-task (the command list at
        ``wos:ui:command:<iid>`` is only drained between tasks, which is too
        late for a restart that wants the current scenario killed now).
        """
        import json as _json

        client = self._redis
        if client is None:
            return
        channel = f"wos:events:abort_task:{self._cfg.instance_id}"
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(channel)
        except Exception:
            logger.exception("Failed to subscribe to %s", channel)
            return

        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None or msg.get("type") != "message":
                    continue
                raw = msg.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                try:
                    payload = _json.loads(raw) if raw else {}
                except _json.JSONDecodeError:
                    payload = {}
                reason = str(payload.get("reason") or "external abort request")
                await self._cancel_current_task(reason)
        except asyncio.CancelledError:  # noqa: TRY203 — explicit cancellation pass-through
            raise
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(channel)
            with suppress(Exception):
                await pubsub.aclose()

    async def _clear_pending_approval_on_boot(self) -> None:
        """Drop any leftover pending click-approval slot at worker boot.

        Approvals are stored in a single per-instance Redis slot
        (``wos:ui:click_approval:current:<instance_id>``). When the worker
        process dies or is restarted, that slot survives — and on the next
        boot the new worker would happily block on a request whose owning
        task is gone. The operator's only recourse would be approving an
        action the new bot has no context for.

        Reaping the slot at boot favours correctness over preserving stale
        operator intent: if the underlying screen state still triggers the
        same action, an overlay tick or scenario step will re-publish a
        fresh approval moments later.
        """
        if self._redis is None:
            return
        current_key = f"wos:ui:click_approval:current:{self._cfg.instance_id}"
        try:
            removed = await self._redis.delete(current_key)  # type: ignore[union-attr]
        except Exception:
            logger.debug("approval cleanup at boot: delete failed", exc_info=True)
            return
        if removed:
            logger.info(
                "Click approval: reaped pending slot for %s at worker boot",
                self._cfg.instance_id,
            )

    async def _fail_stuck_running_on_boot(self) -> None:
        """Fail any task left in the 'running' slot from a previous worker process.

        ``_run_one_queue_item`` pulls a ``QueueItem`` from the sorted set, then
        publishes ``wos:queue:running:<instance_id>`` (and updates the instance
        state hash) so the UI can show what's executing. If the worker dies
        mid-task the running key and state-hash fields outlive the process,
        and the UI keeps rendering the dead task as still running until the
        180s TTL expires — meanwhile the underlying ``QueueItem`` is gone
        (already dequeued) and nothing re-enqueues it.

        Two signals get checked at boot:

        1. ``wos:queue:running:<iid>`` payload — carries the full QueueItem
           snapshot, but has a 180s TTL.
        2. ``wos:instance:<iid>:state`` hash fields (``current_task_*`` /
           ``current_scenario``) — no TTL, so they survive long restarts.

        If only #2 is present (worker died and restart happened more than 180s
        later, so the running key TTL'd away), synthesize the orphan record
        from the state hash so the history still gets a ``worker_restart``
        entry instead of the task vanishing silently. ``_connect`` deliberately
        leaves those fields alone so this fallback has data to work with.

        Re-enqueueing isn't safe: the running payload doesn't carry full
        ``QueueItem`` context (``start_step_index``, original ``next_run_at``),
        and resuming a hand-pointer or DSL scenario from a middle step blind
        risks acting on the wrong screen state. Instead, mark the task failed
        in history so the UI shows it ended, and let the normal re-trigger
        paths (overlay tick, cron, scheduler) push fresh work. The startup
        overlay tick that runs moments later will re-detect anything still
        visible on screen.
        """
        if self._redis is None:
            return
        running_key = _running_key_for_instance(self._cfg.instance_id)
        state_key = _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id)
        try:
            raw = await self._redis.get(running_key)  # type: ignore[union-attr]
        except Exception:
            logger.debug("stuck task cleanup at boot: get failed", exc_info=True)
            return

        import json

        data: dict[str, Any] | None = None
        recovered_from = "running_key"
        if raw:
            try:
                txt = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
                data = json.loads(txt)
            except Exception:
                logger.debug(
                    "stuck task cleanup at boot: payload parse failed", exc_info=True
                )
                data = {}
        else:
            # Running key TTL'd away — fall back to the state hash. Any
            # ``current_task_*`` / ``current_scenario`` field still set is
            # evidence of an in-flight task at last shutdown.
            data = await self._read_orphan_from_state_hash(state_key)
            if data is None:
                return
            recovered_from = "state_hash"

        if not isinstance(data, dict):
            data = {}

        started_at = float(data.get("started_at") or 0.0)
        finished_at = float(time.time())
        row = {
            "task_id": str(data.get("task_id") or ""),
            "task_type": str(data.get("task_type") or ""),
            "scenario": str(data.get("task_type") or ""),
            "player_id": str(data.get("player_id") or ""),
            "instance_id": self._cfg.instance_id,
            "priority": data.get("priority"),
            "region": str(data.get("region") or ""),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": max(0.0, finished_at - started_at) if started_at else 0.0,
            "success": False,
            "error": "worker restarted mid-task",
            "reason": "worker_restart",
            "metadata": {},
        }
        try:
            history_key = _history_key_for_instance(self._cfg.instance_id)
            await self._redis.lpush(history_key, json.dumps(row, ensure_ascii=False, default=str))  # type: ignore[union-attr]
            await self._redis.ltrim(history_key, 0, 49)  # type: ignore[union-attr]
            await self._redis.expire(history_key, 60 * 60 * 24 * 7)  # type: ignore[union-attr]
        except Exception:
            logger.debug("stuck task cleanup at boot: history write failed", exc_info=True)

        try:
            await self._redis.delete(running_key)  # type: ignore[union-attr]
        except Exception:
            logger.debug("stuck task cleanup at boot: delete failed", exc_info=True)

        try:
            await self._redis.hset(  # type: ignore[union-attr]
                state_key,
                mapping={
                    "current_task_id": "",
                    "current_task_type": "",
                    "current_task_player": "",
                    "current_task_started_at": "",
                    "current_task_region": "",
                    "current_task_threshold": "",
                    "current_task_score": "",
                    "current_task_match_top_left_x": "",
                    "current_task_match_top_left_y": "",
                    "current_task_template_w": "",
                    "current_task_template_h": "",
                    "current_task_tap_match_x_pct": "",
                    "current_task_tap_match_y_pct": "",
                    "current_scenario": "",
                    "last_overlay_match_threshold": "",
                    "last_overlay_match_score": "",
                    "last_overlay_match_region": "",
                },
            )
        except Exception:
            logger.debug("stuck task cleanup at boot: state hash clear failed", exc_info=True)

        logger.info(
            "Stuck task: failed orphaned %s (id=%s) for %s at worker boot (via %s)",
            row["task_type"] or "?",
            row["task_id"] or "?",
            self._cfg.instance_id,
            recovered_from,
        )

    def _remove_stale_temporal_screenshots_on_boot(self) -> None:
        """Delete rolling/approval PNGs left over from the previous worker run.

        The analyzer reads ``temporal/<iid>_current_state.png`` on
        every overlay tick. After a restart that file still holds the last
        frame from the dead process; if the new capture is slow (device offline,
        ADB reconnect, foreground-the-game wait), the analyzer can match
        against a stale screen and publish phantom UI state. Same story for
        the approval snapshot — the Redis slot is already reaped above, so
        the matching PNG is orphan UI clutter.

        Labeling captures (``<iid>_shot_*.png``) are pending operator work
        and are deliberately left in place.
        """
        root = repo_root()
        targets = [
            temporal_png_abs_path(
                root, rolling_preview_basename(self._cfg.instance_id)
            ),
            temporal_png_abs_path(root, f"{self._cfg.instance_id}_approval_current"),
        ]
        for path in targets:
            try:
                if not path.exists():
                    continue
                path.unlink()
            except OSError:
                logger.debug(
                    "Temporal screenshot cleanup at boot: unlink %s failed",
                    path,
                    exc_info=True,
                )
                continue
            logger.info(
                "Temporal screenshot: removed stale %s at worker boot",
                path.name,
            )

    async def _read_orphan_from_state_hash(
        self, state_key: str
    ) -> dict[str, Any] | None:
        """Synthesize an orphan-task payload from the instance state hash.

        Used when ``wos:queue:running:<iid>`` has TTL'd away but the state hash
        still carries ``current_task_*`` / ``current_scenario`` from a worker
        that died more than 180s ago. Returns ``None`` when the hash carries
        no in-flight evidence (legit clean boot).
        """
        if self._redis is None:
            return None
        try:
            raw_state = await self._redis.hgetall(state_key)
        except Exception:
            logger.debug(
                "stuck task cleanup at boot: state hash read failed", exc_info=True
            )
            return None
        if not raw_state:
            return None
        state_map: dict[str, str] = {}
        for k, v in raw_state.items():
            ks = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
            vs = (
                v.decode()
                if isinstance(v, (bytes, bytearray))
                else (str(v) if v is not None else "")
            )
            state_map[ks] = vs

        task_id = state_map.get("current_task_id", "").strip()
        task_type = (
            state_map.get("current_task_type", "").strip()
            or state_map.get("current_scenario", "").strip()
        )
        player_id = state_map.get("current_task_player", "").strip()
        region = state_map.get("current_task_region", "").strip()
        # All four empty → nothing to recover, legit clean boot.
        if not (task_id or task_type or player_id or region):
            return None
        try:
            started_at = float(state_map.get("current_task_started_at") or 0.0)
        except (TypeError, ValueError):
            started_at = 0.0
        return {
            "task_id": task_id,
            "task_type": task_type,
            "player_id": player_id,
            "region": region,
            "started_at": started_at,
            "priority": None,
        }

    async def _seed_startup_tasks(self) -> None:
        """Clear stale boot queue items; optionally enqueue ``_STARTUP_SEED_TASKS``.

        Identity (``who_i_am``) is enqueued separately by
        ``_maybe_enqueue_who_i_am_when_active_player_missing`` right after this
        call, so any stale ``who_i_am`` left in the queue from the prior worker
        is dropped here before the fresh one is published.
        """
        if self._queue is None:
            return
        root = repo_root()
        for stale_type in _startup_stale_boot_task_types(root):
            try:
                removed = await self._queue.remove_by_task_type(
                    stale_type, self._cfg.instance_id
                )
                if removed:
                    logger.info(
                        "Startup seed: removed %d stale %r item(s) from queue",
                        removed,
                        stale_type,
                    )
            except Exception:
                logger.exception(
                    "startup seed: failed to remove stale items for %s", stale_type
                )
        if not _STARTUP_SEED_TASKS:
            return
        now = time.time()
        for scenario_key, priority in _STARTUP_SEED_TASKS:
            run_at = now

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
        self._worker_boot_at = time.monotonic()
        self._boot_interactive_at = 0.0
        logger.info("Worker started for instance %s", self._cfg.instance_id)
        # Reap any pending approval from a previous session. After restart we've
        # forgotten what task owned it (``self.player_id``, in-memory state, all
        # gone) so the request is effectively orphaned — leaving it in place
        # would block the new worker on the first ``_require_approval`` call.
        # Operator can re-approve from the UI if the underlying intent still
        # applies; what we MUST avoid is silent permadeadlock.
        await self._clear_pending_approval_on_boot()
        # Same idea for a task that was mid-execution when the previous worker
        # died: the running key + state-hash fields outlive the process, but
        # the QueueItem is gone. Mark it failed in history and wipe the slot;
        # overlay tick / cron / scheduler will push fresh work as needed.
        await self._fail_stuck_running_on_boot()
        # Remove rolling/approval PNGs from the previous run so the first
        # analyzer tick can't match against a stale frame while the new
        # capture is still warming up.
        self._remove_stale_temporal_screenshots_on_boot()
        logger.info(
            "Capture config for %s: backend=%s serial=%s adb_executable=%s",
            self._cfg.instance_id,
            getattr(self._cfg, "screenshot_backend", "scrcpy"),
            self._cfg.bluestacks_window_title,
            self._worker_adb_bin(),
        )
        root = repo_root()
        rolling_path = temporal_png_abs_path(
            root, rolling_preview_basename(self._cfg.instance_id)
        )
        logger.info(
            "Rolling preview %s: interval=%.2fs path=%s",
            self._cfg.instance_id,
            float(self._settings.worker.device_reference_snapshot_interval_seconds),
            rolling_path,
        )
        game_ready = False
        try:
            try:
                game_ready = await self._run_blocking(self._ensure_whiteout_at_worker_start)
            except Exception:
                logger.exception(
                    "Whiteout foreground check/launch failed for instance %s",
                    self._cfg.instance_id,
                )
            # ``_ensure_whiteout_at_worker_start`` sets ``_ui_paused`` when the device
            # is offline or the game could not be brought to foreground in time.
            # Mirror that to Redis so the UI shows "paused (auto)" immediately, and
            # skip overlay capture (would throw or tap the launcher). Seeding still
            # runs — queued tasks wait on the pause gate until watchdog resumes.
            if self._ui_paused and self._redis is not None:
                pause_reason = (
                    getattr(self, "_startup_pause_reason", "") or "device offline (ADB)"
                )
                mapping: dict[str, str] = {
                    "paused": "1",
                    "last_error": pause_reason,
                }
                # Only device-offline pauses are auto-resumed by the health watchdog
                # when ADB comes back. Game-not-ready stays paused until foreground.
                if pause_reason == "device offline (ADB)":
                    mapping["auto_paused"] = "1"
                else:
                    mapping["auto_paused"] = "0"
                with suppress(Exception):
                    await self._redis.hset(  # type: ignore[union-attr]
                        _INST_STATE_KEY_FMT.format(instance_id=self._cfg.instance_id),
                        mapping=mapping,
                    )
            # Publish the first rolling preview PNG before the startup overlay
            # probe. The overlay pass can be slow on a cold detector/import path,
            # but the live UI only needs a current frame to stop showing an empty
            # screenshot panel.
            await self._device_reference_snapshot_tick(analyze=False)
            if game_ready:
                # Run startup overlay before queue cleanup so any visible popup
                # enqueues its own dismiss/claim scenario before ``who_i_am``.
                await self._startup_overlay_tick()
            await self._seed_startup_tasks()
            if game_ready:
                await self._maybe_enqueue_who_i_am_when_active_player_missing()
            # Keep publishing after the primed first frame. The loop owns ongoing
            # screen/overlay analysis; the startup overlay above is a one-shot.
            self._rolling_snapshot_task = asyncio.create_task(
                self._device_reference_snapshot_loop(),
                name=f"refsnap-{self._cfg.instance_id}",
            )
            self._abort_task_listener_task = asyncio.create_task(
                self._run_abort_task_listener(),
                name=f"abort-task-{self._cfg.instance_id}",
            )
            last_heartbeat = 0.0

            try:
                while True:
                    # Heartbeat for UI: lets us distinguish "stale restarting" from "actually down".
                    now_m = time.monotonic()
                    if now_m - last_heartbeat >= 2.0 and self._redis is not None:
                        try:
                            await self._redis.hset(
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
            al = self._abort_task_listener_task
            self._abort_task_listener_task = None
            if al is not None and not al.done():
                al.cancel()
                with suppress(asyncio.CancelledError):
                    await al
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
            analyze = self._rolling_analyze_task
            self._rolling_analyze_task = None
            if analyze is not None and not analyze.done():
                analyze.cancel()
                with suppress(asyncio.CancelledError):
                    await analyze
            startup_overlay = self._startup_overlay_task
            self._startup_overlay_task = None
            if startup_overlay is not None and not startup_overlay.done():
                startup_overlay.cancel()
                with suppress(asyncio.CancelledError):
                    await startup_overlay
            self._blocking_executor_live = False
            try:
                self._blocking_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.debug("blocking thread pool shutdown failed", exc_info=True)
            try:
                self._rolling_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.debug("rolling thread pool shutdown failed", exc_info=True)
            await self._disconnect_redis()

    # _run_one_queue_item and _reschedule_if_needed are provided by InstanceWorkerTasksMixin
