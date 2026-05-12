"""Independent OS process: periodic ADB check that Whiteout is foreground; restarts if not.

Runs as ``python -m worker.game_health_watchdog`` — spawned by ``ui.bot_services`` together
with the embedded supervisor so checks are not blocked by long DSL tasks.
"""

from __future__ import annotations

import contextlib
import json
import logging
import signal
import sys
import threading
import time

import redis

from actions.tap import AdbController, BotActions, canonical_adb_serial
from capture.adb_screencap import DEFAULT_ADB_BIN
from config.loader import get_settings
from config.logging_stdout import setup_stdout_logging
from config.redis_health import sync_redis_from_url_or_exit
from config.startup_validation import assert_startup_configs_valid
from fsm.states import InstanceState

logger = logging.getLogger(__name__)

_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


def _push_instance_command(r: redis.Redis, instance_id: str, cmd: dict[str, object]) -> None:
    """Mirror of ``ui.redis_client.push_instance_command`` without pulling streamlit."""
    with contextlib.suppress(redis.RedisError):
        r.lpush(f"wos:ui:command:{instance_id}", json.dumps(cmd))


def _publish_abort_task(r: redis.Redis, instance_id: str, reason: str) -> None:
    """Pubsub: kill the worker's in-flight task immediately.

    The command list at ``wos:ui:command:<iid>`` only drains between tasks,
    so a slow scenario keeps running while the game is being force-stopped.
    Pubsub is read by ``InstanceWorker._run_abort_task_listener`` and reaches
    the worker mid-task.
    """
    with contextlib.suppress(redis.RedisError):
        r.publish(
            f"wos:events:abort_task:{instance_id}",
            json.dumps({"reason": reason}),
        )


_FOREGROUND_VERIFY_TIMEOUT_S = 20.0
_FOREGROUND_VERIFY_INTERVAL_S = 1.0
_POST_RESTART_GRACE_S = 10.0


def restart_application_after_health_failure(instance_id: str, r: redis.Redis) -> None:
    """Mirror ``InstanceWorkerHealthMixin._restart_instance`` using sync Redis + blocking ADB.

    Pauses the worker (scenarios + analyzers gated by ``_ui_paused``) before the
    restart, waits for the game to come back foreground, gives a fixed grace
    window for the splash/login to settle, then resumes. Without the pause an
    in-flight scenario keeps tapping a force-stopped screen and the rolling
    snapshot loop keeps screencap'ing the launcher.
    """
    key = _INST_STATE_KEY_FMT.format(instance_id=instance_id)
    lock_key = f"wos:instance:{instance_id}:lock"
    ba = BotActions()

    # 1a. Kill the in-flight task NOW (pubsub reaches the worker mid-task,
    # unlike the command list which only drains between tasks).
    _publish_abort_task(r, instance_id, "watchdog: game not foreground")
    # 1b. Pause the worker BEFORE touching the game so scenarios/analyzers stop.
    _push_instance_command(r, instance_id, {"cmd": "pause"})
    with contextlib.suppress(redis.RedisError):
        r.hset(
            key,
            mapping={
                "state": str(InstanceState.RESTARTING),
                "paused": "1",
                "auto_paused": "1",
                "last_error": "",
            },
        )
    # Give the worker a moment to drain the pause command and let the
    # in-flight task observe the cancellation.
    time.sleep(0.5)
    with contextlib.suppress(redis.RedisError):
        r.delete(lock_key)

    try:
        try:
            ba.restart_application(instance_id)
            time.sleep(3.0)
            ba.ensure_game_foreground(instance_id)
        except Exception:
            logger.exception("Watchdog: restart_application failed on %s", instance_id)
            with contextlib.suppress(redis.RedisError):
                r.hset(
                    key,
                    mapping={
                        "state": str(InstanceState.CRASHED),
                        "last_error": "restart_application failed (see logs)",
                    },
                )
            return

        # 2. Poll until the game is foreground or we hit the budget.
        deadline = time.monotonic() + _FOREGROUND_VERIFY_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if ba.is_game_foreground(instance_id):
                    break
            except Exception:
                logger.debug(
                    "Watchdog: is_game_foreground probe failed on %s",
                    instance_id,
                    exc_info=True,
                )
            time.sleep(_FOREGROUND_VERIFY_INTERVAL_S)
        else:
            logger.warning(
                "Watchdog: %s did not return to foreground within %.1fs — resuming anyway",
                instance_id,
                _FOREGROUND_VERIFY_TIMEOUT_S,
            )

        # 3. Splash/login grace so the first scenario tap doesn't land on a loader.
        logger.info(
            "Watchdog: %s back in foreground — settling for %.0fs before resume",
            instance_id,
            _POST_RESTART_GRACE_S,
        )
        time.sleep(_POST_RESTART_GRACE_S)

        with contextlib.suppress(redis.RedisError):
            r.hset(key, mapping={"state": str(InstanceState.READY), "last_error": ""})
    finally:
        # 4. Always resume — even on a crashed path we want to clear the flag we
        # set in step 1; otherwise the worker would stay paused forever waiting
        # for someone to send ``{"cmd": "resume"}``.
        _push_instance_command(r, instance_id, {"cmd": "resume"})
        with contextlib.suppress(redis.RedisError):
            r.hset(key, mapping={"paused": "0", "auto_paused": "0"})


def run_forever(stop: threading.Event | None = None) -> None:
    setup_stdout_logging()
    assert_startup_configs_valid()
    settings = get_settings()
    interval = max(1, int(settings.worker.health_check_interval_seconds))
    r = sync_redis_from_url_or_exit(settings.redis.url, decode_responses=True)
    ba = BotActions()

    logger.info(
        "Game health watchdog: interval=%ss instances=%s",
        interval,
        [i.instance_id for i in settings.instances],
    )

    ev = stop if stop is not None else threading.Event()
    adb_bin = (settings.worker.adb_executable or "").strip() or DEFAULT_ADB_BIN

    while not ev.is_set():
        # One ``adb devices`` per tick — anything not in ``device`` state is
        # presumed offline (BlueStacks closed, lost USB, user kill-server, …).
        # Instead of hammering the failing serial each tick (which throws
        # ``AdbController._verify_available`` and stains the log with tracebacks),
        # flip the instance into the existing pause state and flip it back when
        # the device returns.
        try:
            live_canonical = {
                canonical_adb_serial(s) for s in AdbController.list_devices(adb_bin)
            }
        except Exception:
            logger.exception("Watchdog: failed to enumerate ADB devices")
            live_canonical = set()

        for inst in settings.instances:
            if ev.is_set():
                break
            iid = inst.instance_id
            key = _INST_STATE_KEY_FMT.format(instance_id=iid)
            serial_canon = canonical_adb_serial(inst.bluestacks_window_title)
            is_live = serial_canon in live_canonical

            try:
                state_row = r.hgetall(key) or {}
            except redis.RedisError:
                state_row = {}
            is_paused = str(state_row.get("paused") or "").strip() == "1"
            was_auto_paused = str(state_row.get("auto_paused") or "").strip() == "1"

            if not is_live:
                if not is_paused:
                    # Cmd goes via the worker's command channel so its in-memory
                    # ``_ui_paused`` flag flips too; ``auto_paused`` lets us tell
                    # an operator-initiated pause apart from this one (we only
                    # auto-resume what we paused).
                    _push_instance_command(r, iid, {"cmd": "pause"})
                    with contextlib.suppress(redis.RedisError):
                        r.hset(
                            key,
                            mapping={
                                "auto_paused": "1",
                                "last_error": "device offline (ADB)",
                            },
                        )
                    logger.info(
                        "Watchdog: %s offline (serial=%s) — paused",
                        iid,
                        inst.bluestacks_window_title,
                    )
                continue

            if is_paused and was_auto_paused:
                _push_instance_command(r, iid, {"cmd": "resume"})
                with contextlib.suppress(redis.RedisError):
                    r.hset(
                        key,
                        mapping={"auto_paused": "0", "last_error": ""},
                    )
                logger.info(
                    "Watchdog: %s back online (serial=%s) — resumed",
                    iid,
                    inst.bluestacks_window_title,
                )
                # Skip the foreground check this tick; the worker just woke up.
                continue

            if is_paused:
                # Paused by the operator — don't touch.
                continue

            try:
                if ba.is_game_foreground(iid):
                    continue
                logger.warning(
                    "Watchdog: Whiteout not foreground on %s — restarting application",
                    iid,
                )
                restart_application_after_health_failure(iid, r)
            except Exception:
                logger.exception("Watchdog: step failed for %s", iid)

        if ev.wait(timeout=float(interval)):
            break


def main() -> None:
    stop = threading.Event()

    def _handle_sig(_signum: int, _frame: object | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _handle_sig)  # type: ignore[attr-defined]

    try:
        run_forever(stop)
    finally:
        logger.info("Game health watchdog exiting")


if __name__ == "__main__":
    main()
