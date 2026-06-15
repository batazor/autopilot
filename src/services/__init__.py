"""Public service registry — APP-scope singletons + per-instance factory.

Replaces the former ``di/`` Dishka container. Lazy module-level singletons
keep imports cheap (Streamlit can render a page that only needs Settings
without booting Redis), while :func:`init_app_services` and
:func:`aclose_app_services` are the explicit lifecycle hooks the embedded
supervisor and standalone entrypoints call on start / stop.

State lives in :mod:`services._state` so Streamlit hot-reloading the public
``services`` module doesn't reset live singletons (the OCR client,
scheduler Redis connection, scenario watcher, ...). The state module is
intentionally small so its own mtime almost never changes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from services import _state

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import redis as redis_sync
    import redis.asyncio as aioredis

    from adb.bot_actions import BotActions
    from config.loader import InstanceConfig, Settings
    from ocr.client import OcrClient
    from scheduler.queue import RedisQueue
    from scheduler.runner import SchedulerRunner
    from worker.instance_worker import InstanceWorker


# Keys used in :mod:`app._state`. Centralized so typos don't silently
# create dangling slots that never get cleaned up by ``aclose``.
_K_OCR = "ocr_client"
_K_BOT_ACTIONS = "bot_actions"
_K_SCHED_ASYNC_REDIS = "scheduler_async_redis"
_K_SCHED_WAKE_REDIS = "scheduler_wake_redis"
_K_SCHED_QUEUE = "scheduler_queue"
_K_SCHED_RUNNER = "scheduler_runner"
_K_ACTIVE_GAME = "active_game"


def bind_active_game(game: str) -> None:
    """Set the game id this worker process serves.

    Called once per worker child by :func:`worker.supervisor._worker_process`
    after it resolves ``instance_config.game``. Downstream getters
    (navigator, loaders) read this via
    :func:`get_active_game` so they walk only the active game's tree.
    """
    _state.set_(_K_ACTIVE_GAME, (game or "").strip())


def get_active_game() -> str:
    """Active game id for this process, or the registry default if unbound."""
    from config.games import default_game

    g = _state.get(_K_ACTIVE_GAME)
    if isinstance(g, str) and g:
        return g
    return default_game()


def resolve_effective_game(instance_config: InstanceConfig) -> str:
    """Game id the worker should serve for ``instance_config``.

    The device can already be running a *different* game than the one its DB row
    configures (operator switched the foreground app, stale row, etc.). Probe
    the device: if a *known* game is actually running and differs from the
    configured game, adopt it — and update the matching ``Settings.instances``
    entry so launch / foreground checks (which read the game from Settings, not
    from the ``InstanceConfig`` handed to the worker) target the same game.

    Falls back to the configured game when the device is offline or no known
    game is running, so a fresh boot still launches the configured game.

    Must run after :func:`init_app_services` (it reads loaded ``Settings``).
    """
    from dataclasses import replace

    from adb import AdbController
    from adb.screencap import DEFAULT_ADB_BIN
    from config.games import default_game

    configured = (instance_config.game or "").strip() or default_game()
    settings = get_settings()
    adb_bin = (settings.worker.adb_executable or "").strip() or DEFAULT_ADB_BIN
    try:
        controller = AdbController(
            instance_config.instance_id,
            instance_config.bluestacks_window_title,
            adb_bin=adb_bin,
        )
        running = controller.detect_running_game()
    except Exception:
        logger.debug(
            "resolve_effective_game: device probe failed for %s — using configured game %s",
            instance_config.instance_id,
            configured,
            exc_info=True,
        )
        return configured

    if not running or running == configured:
        return configured

    logger.warning(
        "Device %s is running %s but is configured for %s — adopting %s for this run",
        instance_config.instance_id,
        running,
        configured,
        running,
    )
    for i, inst in enumerate(settings.instances):
        if inst.instance_id == instance_config.instance_id:
            settings.instances[i] = replace(inst, game=running)
            break
    return running


# ---- lifecycle -------------------------------------------------------------


async def init_app_services() -> None:
    """Bind :class:`Settings` and warm-init the OCR client.

    Idempotent. Embedded UI calls this once on supervisor boot so DSL tasks
    and overlay engine can synchronously call :func:`get_ocr_client`.
    """
    from config.loader import load_settings, set_settings

    set_settings(load_settings())
    # Warm the OCR client so the first overlay tick / DSL ``ocr:`` step
    # doesn't pay constructor cost on the hot path.
    get_ocr_client()


async def aclose_app_services() -> None:
    """Tear down APP-scope services that hold OS resources."""
    try:
        from adb.scrcpy import close_all_scrcpy_clients

        close_all_scrcpy_clients()
    except Exception:
        logger.exception("aclose_app_services: scrcpy close failed")

    if (runner_redis := _state.pop(_K_SCHED_ASYNC_REDIS)) is not None:
        try:
            await runner_redis.aclose()
        except Exception:
            logger.exception("aclose_app_services: async Redis aclose failed")

    if (sync_redis := _state.pop(_K_SCHED_WAKE_REDIS)) is not None:
        try:
            sync_redis.close()
        except Exception:
            logger.exception("aclose_app_services: sync Redis close failed")

    # Stateless services — drop refs so the next ``init`` rebuilds against
    # the current Settings.
    for k in (_K_OCR, _K_BOT_ACTIONS, _K_SCHED_QUEUE, _K_SCHED_RUNNER):
        _state.pop(k)


# ---- APP-scope getters -----------------------------------------------------


def get_settings() -> Settings:
    """Active :class:`Settings`. Auto-loads on first call so tests and
    standalone scripts don't need to call ``init_app_services`` first."""
    from config.loader import get_settings as _get
    from config.loader import load_settings, set_settings

    try:
        return _get()
    except RuntimeError:
        s = load_settings()
        set_settings(s)
        return s


def get_repo_root() -> Path:
    from config.paths import repo_root

    return repo_root()


def get_ocr_client() -> OcrClient:
    """Lazy :class:`OcrClient`. Tests that monkeypatch ``ocr.client.OcrClient``
    with a zero-arg stub still work — we fall back to no-args construction."""
    import ocr.client as ocr_mod

    ctor = ocr_mod.OcrClient
    if (c := _state.get(_K_OCR)) is not None:
        if not isinstance(ctor, type) or type(c) is ctor:
            return c
        # Streamlit reloads and pytest monkeypatches can replace the OcrClient
        # class while the APP-scope singleton survives. Rebuild against the
        # currently imported class instead of leaking stale OCR behavior.
        _state.pop(_K_OCR)

    try:
        c = ctor(get_settings())
    except TypeError:
        c = ctor()  # ty: ignore[missing-argument]
    _state.set_(_K_OCR, c)
    return c


def is_ocr_client_ready() -> bool:
    return _state.has(_K_OCR)


def get_bot_actions() -> BotActions:
    if (b := _state.get(_K_BOT_ACTIONS)) is not None:
        return b
    from adb.bot_actions import BotActions

    b = BotActions(get_settings())
    _state.set_(_K_BOT_ACTIONS, b)
    return b


# ---- Scheduler infra (async lifecycle) ------------------------------------


async def get_scheduler_async_redis() -> aioredis.Redis:
    if (c := _state.get(_K_SCHED_ASYNC_REDIS)) is not None:
        return c
    import redis.asyncio as aioredis

    from config.redis_health import ping_async_redis_or_exit
    from config.redis_metrics import instrument_redis_client

    settings = get_settings()
    c = aioredis.from_url(settings.redis.url, socket_connect_timeout=5.0)
    instrument_redis_client(c, component="scheduler")
    await ping_async_redis_or_exit(c, url=settings.redis.url)
    _state.set_(_K_SCHED_ASYNC_REDIS, c)
    return c


def get_scheduler_wake_redis() -> redis_sync.Redis:
    if (c := _state.get(_K_SCHED_WAKE_REDIS)) is not None:
        return c
    import redis as redis_sync

    from config.redis_metrics import instrument_redis_client

    c = redis_sync.Redis.from_url(get_settings().redis.url, socket_connect_timeout=5.0)
    instrument_redis_client(c, component="scheduler")
    _state.set_(_K_SCHED_WAKE_REDIS, c)
    return c


async def get_scheduler_queue() -> RedisQueue:
    if (q := _state.get(_K_SCHED_QUEUE)) is not None:
        return q
    from scheduler.queue import RedisQueue

    redis = await get_scheduler_async_redis()
    q = RedisQueue(redis, get_settings())
    _state.set_(_K_SCHED_QUEUE, q)
    return q


async def get_scheduler_runner() -> SchedulerRunner:
    if (r := _state.get(_K_SCHED_RUNNER)) is not None:
        return r
    from scheduler.runner import SchedulerRunner

    redis = await get_scheduler_async_redis()
    queue = await get_scheduler_queue()
    r = SchedulerRunner(
        get_settings(),
        redis=redis,
        queue=queue,
        wake_sync=get_scheduler_wake_redis(),
    )
    _state.set_(_K_SCHED_RUNNER, r)
    return r


# ---- Per-instance worker (former REQUEST scope) ---------------------------


@asynccontextmanager
async def instance_worker_session(
    instance_config: InstanceConfig,
) -> AsyncIterator[InstanceWorker]:
    """Build an :class:`InstanceWorker` with its own async Redis + queue, and
    aclose the Redis client on exit. One session per instance per supervisor
    run — InstanceWorker.run() blocks for the lifetime of the worker."""
    import redis.asyncio as aioredis

    from config.redis_health import ping_async_redis_or_exit
    from config.redis_metrics import instrument_redis_client
    from scheduler.queue import RedisQueue
    from worker.instance_worker import InstanceWorker

    settings = get_settings()
    redis = aioredis.from_url(settings.redis.url, socket_connect_timeout=5.0)
    instrument_redis_client(redis, component="worker")
    await ping_async_redis_or_exit(redis, url=settings.redis.url)
    try:
        queue = RedisQueue(redis, settings)
        worker = InstanceWorker(
            instance_config,
            settings=settings,
            bot_actions=get_bot_actions(),
            ocr_client=get_ocr_client(),
            redis=redis,
            queue=queue,
        )
        yield worker
    finally:
        with suppress(Exception):
            await redis.aclose()
