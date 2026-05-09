"""Independent OS process: periodic ADB check that Whiteout is foreground; restarts if not.

Runs as ``python -m worker.game_health_watchdog`` — spawned by ``ui.bot_services`` together
with the embedded supervisor so checks are not blocked by long DSL tasks.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

import redis

from actions.tap import BotActions
from config.loader import get_settings
from config.logging_stdout import setup_stdout_logging
from config.startup_validation import assert_startup_configs_valid
from fsm.states import InstanceState

logger = logging.getLogger(__name__)

_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


def restart_application_after_health_failure(instance_id: str, r: redis.Redis) -> None:
    """Mirror ``InstanceWorkerHealthMixin._restart_instance`` using sync Redis + blocking ADB."""
    key = _INST_STATE_KEY_FMT.format(instance_id=instance_id)
    lock_key = f"wos:instance:{instance_id}:lock"
    ba = BotActions()
    try:
        r.hset(key, mapping={"state": str(InstanceState.RESTARTING), "last_error": ""})
    except redis.RedisError:
        logger.debug("watchdog: redis hset RESTARTING failed", exc_info=True)
    try:
        r.delete(lock_key)
    except redis.RedisError:
        pass
    try:
        ba.restart_application(instance_id)
        time.sleep(3.0)
        ba.ensure_game_foreground(instance_id)
    except Exception:
        logger.exception("Watchdog: restart_application failed on %s", instance_id)
        try:
            r.hset(
                key,
                mapping={
                    "state": str(InstanceState.CRASHED),
                    "last_error": "restart_application failed (see logs)",
                },
            )
        except redis.RedisError:
            pass
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
    r = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    ba = BotActions()

    logger.info(
        "Game health watchdog: interval=%ss instances=%s",
        interval,
        [i.instance_id for i in settings.instances],
    )

    ev = stop if stop is not None else threading.Event()

    while not ev.is_set():
        for inst in settings.instances:
            if ev.is_set():
                break
            iid = inst.instance_id
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
