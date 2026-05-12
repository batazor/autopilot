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


def restart_application_after_health_failure(instance_id: str, r: redis.Redis) -> None:
    """Mirror ``InstanceWorkerHealthMixin._restart_instance`` using sync Redis + blocking ADB."""
    key = _INST_STATE_KEY_FMT.format(instance_id=instance_id)
    lock_key = f"wos:instance:{instance_id}:lock"
    ba = BotActions()
    try:
        r.hset(key, mapping={"state": str(InstanceState.RESTARTING), "last_error": ""})
    except redis.RedisError:
        logger.debug("watchdog: redis hset RESTARTING failed", exc_info=True)
    with contextlib.suppress(redis.RedisError):
        r.delete(lock_key)
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
    try:
        r.hset(key, mapping={"state": str(InstanceState.READY), "last_error": ""})
    except redis.RedisError:
        logger.debug("watchdog: redis hset READY failed", exc_info=True)


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
