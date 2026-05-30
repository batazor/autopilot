"""Independent OS process: periodic ADB check that Whiteout is foreground; restarts if not.

Runs as ``python -m worker.game_health_watchdog`` — spawned by ``dashboard.bot_services`` together
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
from typing import Any

import redis

from adb import AdbController, BotActions, canonical_adb_serial
from adb.screencap import DEFAULT_ADB_BIN
from config.loader import Settings, get_settings, load_settings, set_settings
from config.redis_health import sync_redis_from_url_or_exit
from config.runtime_bootstrap import bootstrap_runtime_observability
from config.startup_validation import assert_startup_configs_valid
from navigation.lifecycle_states import InstanceState

logger = logging.getLogger(__name__)

_INST_STATE_KEY_FMT = "wos:instance:{instance_id}:state"


def _push_instance_command(r: redis.Redis, instance_id: str, cmd: dict[str, object]) -> None:
    """Mirror of ``dashboard.redis_client.push_instance_command`` without the import dep."""
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
_FOREGROUND_FAILURE_RETRIES = 3
_FOREGROUND_FAILURE_RETRY_INTERVAL_S = 2.0


def _is_game_running_after_retries(
    ba: BotActions,
    instance_id: str,
    *,
    stop: threading.Event,
    retries: int = _FOREGROUND_FAILURE_RETRIES,
    retry_interval: float = _FOREGROUND_FAILURE_RETRY_INTERVAL_S,
) -> bool:
    """Return true if the game *process* is alive before restart escalation.

    Aliveness — not the resumed-activity ("foreground") check — is the restart
    criterion. On BlueStacks the foreground parse false-negatives while the game
    runs fine (the host launcher is reported as the top activity), which used to
    force-restart a healthy game. We only restart when the process is genuinely
    dead; a momentary ``pidof`` miss during a relaunch is absorbed by the retries.
    """
    attempts = max(1, int(retries) + 1)
    for attempt in range(1, attempts + 1):
        try:
            if ba.is_game_running(instance_id):
                if attempt > 1:
                    logger.info(
                        "Watchdog: %s game process found on attempt %s/%s",
                        instance_id,
                        attempt,
                        attempts,
                    )
                return True
        except Exception:
            logger.debug(
                "Watchdog: process check attempt %s/%s failed on %s",
                attempt,
                attempts,
                instance_id,
                exc_info=True,
            )

        if attempt >= attempts:
            break

        logger.warning(
            "Watchdog: Whiteout process not found on %s — retrying check %s/%s in %.1fs",
            instance_id,
            attempt,
            attempts - 1,
            retry_interval,
        )
        if stop.wait(timeout=float(retry_interval)):
            return True

    return False


def _capture_restart_context(ba: BotActions, instance_id: str) -> tuple[str, str]:
    """Snapshot *what was on screen* and *how detection failed* at restart time.

    Returns ``(foreground, detection)`` where ``foreground`` is the resumed
    ``pkg/activity`` (the launcher/another app ⇒ a real crash; the game itself ⇒
    a detection flake) and ``detection`` summarises the probe — ``error=…`` means
    "couldn't ask ADB" (transient), ``clean_miss`` means the process is truly gone.
    """
    foreground = ""
    try:
        foreground = ba.current_foreground_activity(instance_id)
    except Exception:
        logger.debug("Watchdog: foreground capture failed on %s", instance_id, exc_info=True)
    detection = "detect_failed"
    try:
        d = ba.detect_game_process(instance_id)
        detail = f"error={d.error}" if d.error else "clean_miss"
        detection = f"method={d.method_used} found={d.found} {detail}"
    except Exception:
        logger.debug("Watchdog: detection detail failed on %s", instance_id, exc_info=True)
    return foreground, detection


def _record_restart_breadcrumb(
    r: redis.Redis, instance_id: str, *, foreground: str, detection: str
) -> None:
    """Persist the restart reason so it survives the ephemeral stdout logs.

    Writes ``last_game_restart_{at,foreground,detection}`` + a running
    ``game_restart_count`` to the instance state hash, and appends a
    ``game.restart`` row to the Debug Timeline.
    """
    ts = time.time()
    key = _INST_STATE_KEY_FMT.format(instance_id=instance_id)
    with contextlib.suppress(redis.RedisError):
        r.hset(
            key,
            mapping={
                "last_game_restart_at": f"{ts:.3f}",
                "last_game_restart_foreground": foreground or "unknown",
                "last_game_restart_detection": detection,
            },
        )
        r.hincrby(key, "game_restart_count", 1)
    with contextlib.suppress(redis.RedisError):
        tl_key = f"wos:debug:timeline:{instance_id}"
        r.lpush(
            tl_key,
            json.dumps(
                {
                    "t": ts,
                    "event": "game.restart",
                    "instance_id": instance_id,
                    "reason": "process_dead_after_retries",
                    "foreground": foreground or "unknown",
                    "detection": detection,
                },
                ensure_ascii=False,
                default=str,
            ),
        )
        r.ltrim(tl_key, 0, 999)


def restart_application_after_health_failure(
    instance_id: str,
    r: redis.Redis,
    settings: Settings,
) -> None:
    """Mirror ``InstanceWorkerHealthMixin._restart_instance`` using sync Redis + blocking ADB.

    Pauses the worker (scenarios + analyzers gated by ``_ui_paused``) before the
    restart, waits for the game to come back foreground, gives a fixed grace
    window for the splash/login to settle, then resumes. Without the pause an
    in-flight scenario keeps tapping a force-stopped screen and the rolling
    snapshot loop keeps screencap'ing the launcher.
    """
    key = _INST_STATE_KEY_FMT.format(instance_id=instance_id)
    lock_key = f"wos:instance:{instance_id}:lock"
    ba = BotActions(settings)

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

    restart_ok = False
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

        # 2. Poll until the game process is back or we hit the budget. Aliveness,
        # not foreground — the BlueStacks resumed-activity parse would otherwise
        # never confirm and we'd always burn the full timeout.
        deadline = time.monotonic() + _FOREGROUND_VERIFY_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if ba.is_game_running(instance_id):
                    break
            except Exception:
                logger.debug(
                    "Watchdog: is_game_running probe failed on %s",
                    instance_id,
                    exc_info=True,
                )
            time.sleep(_FOREGROUND_VERIFY_INTERVAL_S)
        else:
            logger.warning(
                "Watchdog: %s process did not come back within %.1fs — resuming anyway",
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

        # A game relaunch is the only moment a runtime account switch can happen,
        # so clear the cached identity here too — the next rolling tick re-arms
        # ``who_i_am`` to re-verify who is logged in. The durable
        # ``last_active_player`` is kept; the probe overwrites it after a switch.
        with contextlib.suppress(redis.RedisError):
            r.hset(
                key,
                mapping={
                    "state": str(InstanceState.READY),
                    "last_error": "",
                    "active_player": "",
                },
            )
        restart_ok = True
    finally:
        # 4. Resume the worker only when the restart actually succeeded.
        # Keeping the worker paused after a CRASHED restart is the safer
        # default: otherwise the worker resumes against a dead game and
        # taps a loader / launcher until the watchdog re-detects the
        # foreground-missing state one full interval later. The operator
        # (or a higher-level recovery loop) can resume manually via the UI.
        if restart_ok:
            _push_instance_command(r, instance_id, {"cmd": "resume"})
            with contextlib.suppress(redis.RedisError):
                r.hset(key, mapping={"paused": "0", "auto_paused": "0"})


def _reload_settings_if_devices_changed(
    prev_ids: set[str],
) -> tuple[Settings, set[str], bool]:
    """Re-read the device registry so a device registered after the watchdog
    started gets game-health monitoring without a bot restart.

    Returns ``(settings, instance_ids, changed)``. Rebinds the process-wide
    settings only when the instance set actually changed, so the periodic
    re-read stays cheap and ``BotActions`` is only rebuilt on a real change.
    """
    from config.devices import invalidate_device_registry

    invalidate_device_registry()
    fresh = load_settings()
    ids = {i.instance_id for i in fresh.instances}
    changed = ids != prev_ids
    if changed:
        set_settings(fresh)
    return fresh, ids, changed


def run_forever(stop: threading.Event | None = None) -> None:
    bootstrap_runtime_observability("health-watchdog")
    assert_startup_configs_valid()
    set_settings(load_settings())
    settings = get_settings()
    interval = max(1, int(settings.worker.health_check_interval_seconds))
    r = sync_redis_from_url_or_exit(settings.redis.url, decode_responses=True)
    from config.redis_metrics import instrument_redis_client

    instrument_redis_client(r, component="health_watchdog")
    ba = BotActions(settings)
    monitored_ids = {i.instance_id for i in settings.instances}

    logger.info(
        "Game health watchdog: interval=%ss instances=%s",
        interval,
        sorted(monitored_ids),
    )

    ev = stop if stop is not None else threading.Event()
    adb_bin = (settings.worker.adb_executable or "").strip() or DEFAULT_ADB_BIN

    while not ev.is_set():
        # Pick up devices registered after startup (and drop unregistered ones)
        # without a watchdog restart — mirrors the worker supervisor's reconcile.
        # ``BotActions`` snapshots ``settings.instances`` at construction, so it
        # must be rebuilt for a new device's serial to resolve.
        fresh, new_ids, changed = _reload_settings_if_devices_changed(monitored_ids)
        if changed:
            added = sorted(new_ids - monitored_ids)
            removed = sorted(monitored_ids - new_ids)
            settings = fresh
            ba = BotActions(settings)
            monitored_ids = new_ids
            logger.info(
                "Watchdog: device set changed (added=%s removed=%s) — monitoring %s",
                added or "-",
                removed or "-",
                sorted(new_ids),
            )
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
                raw_state = r.hgetall(key)
            except redis.RedisError:
                raw_state = {}
            # Sync ``hgetall`` is typed ``Awaitable | dict`` in redis-py stubs.
            state_row: dict[Any, Any] = raw_state if isinstance(raw_state, dict) else {}
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
                try:
                    ba.apply_display_then_launch_game(iid)
                except Exception:
                    logger.exception(
                        "Watchdog: display profile / game launch failed on %s after device online",
                        iid,
                    )
                _push_instance_command(r, iid, {"cmd": "resume"})
                with contextlib.suppress(redis.RedisError):
                    r.hset(
                        key,
                        mapping={"auto_paused": "0", "last_error": ""},
                    )
                logger.info(
                    "Watchdog: %s back online (serial=%s) — game launch attempted, resumed",
                    iid,
                    inst.bluestacks_window_title,
                )
                # Skip the foreground check this tick; worker resumes into main loop.
                continue

            if is_paused and not was_auto_paused:
                last_err = str(state_row.get("last_error") or "")
                if "game not foreground" in last_err:
                    try:
                        if ba.is_game_running(iid):
                            _push_instance_command(r, iid, {"cmd": "resume"})
                            with contextlib.suppress(redis.RedisError):
                                r.hset(
                                    key,
                                    mapping={"paused": "0", "last_error": ""},
                                )
                            logger.info(
                                "Watchdog: %s game process alive after startup pause — resumed",
                                iid,
                            )
                        else:
                            ba.ensure_game_foreground(iid)
                    except Exception:
                        logger.exception(
                            "Watchdog: startup game launch failed on %s", iid
                        )
                # Operator pause or other manual pause — don't touch.
                continue

            if is_paused:
                continue

            try:
                if _is_game_running_after_retries(ba, iid, stop=ev):
                    continue
                foreground, detection = _capture_restart_context(ba, iid)
                logger.warning(
                    "Watchdog: Whiteout process dead on %s after retries — restarting "
                    "application (foreground=%s, detection=%s)",
                    iid,
                    foreground or "unknown",
                    detection,
                )
                _record_restart_breadcrumb(
                    r, iid, foreground=foreground, detection=detection
                )
                restart_application_after_health_failure(iid, r, settings)
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
