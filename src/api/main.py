"""WOS dashboard API (FastAPI)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SHUTDOWN_EXCEPTION_TYPES = (asyncio.CancelledError, KeyboardInterrupt)


def _is_shutdown_exception(exc: BaseException | None) -> bool:
    """True when *exc* (or its cause/context chain) is from server shutdown."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, _SHUTDOWN_EXCEPTION_TYPES):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class _SuppressUvicornShutdownNoiseFilter(logging.Filter):
    """Drop uvicorn ASGI logs for in-flight requests cancelled on Ctrl+C."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info or record.exc_info[0] is None:
            return True
        exc = record.exc_info[1]
        return not (isinstance(exc, BaseException) and _is_shutdown_exception(exc))


def _install_uvicorn_shutdown_noise_filter() -> None:
    shutdown_filter = _SuppressUvicornShutdownNoiseFilter()
    logger = logging.getLogger("uvicorn.error")
    if shutdown_filter not in logger.filters:
        logger.addFilter(shutdown_filter)


_previous_asyncio_exception_handler: object | None = None


def _asyncio_shutdown_exception_handler(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
) -> None:
    exc = context.get("exception")
    if isinstance(exc, BaseException) and _is_shutdown_exception(exc):
        return
    handler = _previous_asyncio_exception_handler
    if callable(handler):
        handler(loop, context)
    else:
        loop.default_exception_handler(context)


def _install_asyncio_shutdown_exception_handler() -> None:
    global _previous_asyncio_exception_handler
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.get_exception_handler() is _asyncio_shutdown_exception_handler:
        return
    _previous_asyncio_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(_asyncio_shutdown_exception_handler)


_install_uvicorn_shutdown_noise_filter()

from config.paths import ensure_repo_on_sys_path  # noqa: E402

ensure_repo_on_sys_path()

from api.routers import (  # noqa: E402 — silence filter must run before transitive ``ui.*`` imports
    adb,
    alliances,
    balance,
    broadcast,
    buildings,
    calendar,
    click_approvals,
    config_reload,
    coord,
    dev_bot,
    dreamscape_onboarding,
    edit_dsl,
    events,
    farm,
    fish_detect,
    gallery,
    gift_codes,
    inference,
    instances,
    labeling,
    modules,
    notify,
    onboarding,
    optimizer,
    overlay_test,
    overview,
    planner,
    players,
    quests,
    queue,
    radar,
    research,
    routes,
    screen,
    version,
    wiki,
)
from api.services.gift_codes_api import run_startup_gift_code_scrape  # noqa: E402


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _install_asyncio_shutdown_exception_handler()
    # With host networking the API shares the host loopback, so starting the adb
    # server here lets the dashboard's ADB scan reach emulators without the user
    # installing adb on the host. Idempotent + non-fatal (see supervisor.main()).
    try:
        from adb import ensure_adb_server
        from config.loader import load_settings

        await asyncio.to_thread(
            ensure_adb_server, load_settings().worker.adb_executable or "adb"
        )
    except Exception:
        logging.getLogger(__name__).debug(
            "ensure_adb_server at API startup failed", exc_info=True
        )
    gift_codes_task = asyncio.create_task(
        run_startup_gift_code_scrape(),
        name="api-gift-codes-startup-scrape",
    )
    try:
        yield
    finally:
        if not gift_codes_task.done():
            gift_codes_task.cancel()
            with suppress(asyncio.CancelledError):
                await gift_codes_task
        try:
            from worker import local_bot

            await asyncio.to_thread(local_bot.stop_local_bot, join_timeout_s=2.0)
        except Exception:
            logging.getLogger(__name__).debug(
                "local bot shutdown during API lifespan failed",
                exc_info=True,
            )


app = FastAPI(title="WOS Autopilot API", version="0.1.0", lifespan=_lifespan)
logger = logging.getLogger(__name__)

_cors_origins = os.environ.get(
    "WOS_API_CORS_ORIGINS",
    "http://127.0.0.1:3000,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(instances.router)
app.include_router(overview.router)
app.include_router(planner.router)
app.include_router(queue.router)
app.include_router(quests.router)
app.include_router(radar.router)
app.include_router(events.router)
app.include_router(farm.router)
app.include_router(players.router)
app.include_router(alliances.router)
app.include_router(labeling.router)
app.include_router(routes.router)
app.include_router(gift_codes.router)
app.include_router(wiki.router)
app.include_router(buildings.router)
app.include_router(calendar.router)
app.include_router(broadcast.router)
app.include_router(research.router)
app.include_router(research.alliance_tech_router)
app.include_router(click_approvals.router)
app.include_router(coord.router)
app.include_router(overlay_test.router)
app.include_router(dreamscape_onboarding.router)
app.include_router(fish_detect.router)
app.include_router(inference.router)
app.include_router(modules.router)
app.include_router(gallery.router)
app.include_router(adb.router)
app.include_router(dev_bot.router)
app.include_router(screen.router)
app.include_router(balance.router)
app.include_router(optimizer.router)
app.include_router(notify.router)
app.include_router(edit_dsl.router)
app.include_router(config_reload.router)
app.include_router(onboarding.router)
app.include_router(version.router)


def _exception_message(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = uuid.uuid4().hex[:12]
    logger.error(
        "Unhandled API error %s %s request_id=%s",
        request.method,
        request.url.path,
        request_id,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Unexpected API error while handling {request.method} {request.url.path}",
            "error": {
                "type": exc.__class__.__name__,
                "message": _exception_message(exc),
            },
            "request_id": request_id,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    from api.deps import get_redis

    redis_status = "ok"
    try:
        get_redis().ping()
    except Exception:
        redis_status = "unreachable"
    overall = "ok" if redis_status == "ok" else "degraded"
    return {"status": overall, "api": "ok", "redis": redis_status}


def main() -> None:
    host = os.environ.get("WOS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("WOS_API_PORT", "8765"))
    graceful_raw = os.environ.get("WOS_API_GRACEFUL_SHUTDOWN", "5").strip()
    graceful_shutdown = float(graceful_raw) if graceful_raw else 5.0
    _install_uvicorn_shutdown_noise_filter()
    try:
        uvicorn.run(
            "api.main:app",
            host=host,
            port=port,
            log_level=os.environ.get("WOS_API_LOG_LEVEL", "info"),
            timeout_graceful_shutdown=graceful_shutdown,
        )
    except KeyboardInterrupt:
        # Uvicorn re-raises SIGINT after shutdown; avoid a second traceback on exit.
        sys.exit(0)


if __name__ == "__main__":
    main()
