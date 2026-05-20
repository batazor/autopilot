"""WOS dashboard API (FastAPI)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SHUTDOWN_EXCEPTION_TYPES = (asyncio.CancelledError, KeyboardInterrupt)


class _StreamlitBareModeFilter(logging.Filter):
    """Drop Streamlit's "no runtime" / "missing ScriptRunContext" log records.

    The API process imports several ``ui/*`` helpers that decorate functions
    with ``@st.cache_data`` / ``@st.cache_resource``. Outside of a Streamlit
    runtime those decorators fall back to ``MemoryCacheStorageManager`` and emit
    warnings that Streamlit itself documents as ignorable in bare mode. They
    flood our uvicorn logs without surfacing actionable info.

    A ``Filter`` (rather than ``setLevel``) is used because Streamlit re-sets
    its per-module logger levels during its own ``get_logger`` cache flow, so
    any ``setLevel`` we do at startup gets clobbered when ``ui.*`` is later
    imported. A filter, attached once to the parent ``streamlit`` logger, runs
    against every emitted record and survives re-init.
    """

    _NEEDLES = (
        "missing ScriptRunContext",
        "No runtime found",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(n in msg for n in self._NEEDLES)


def _silence_streamlit_bare_mode_warnings() -> None:
    """Attach the bare-mode filter to every Streamlit logger that propagates here."""
    streamlit_filter = _StreamlitBareModeFilter()
    # Streamlit per-module loggers all live under ``streamlit.*`` and disable
    # propagation, so attaching only to the root ``streamlit`` parent doesn't
    # work — install on the two known noisy modules plus the catch-all root,
    # then re-install when ``ui.*`` registers new ones below.
    for name in (
        "streamlit",
        "streamlit.runtime.caching.cache_data_api",
        "streamlit.runtime.caching.cache_resource_api",
        "streamlit.runtime.scriptrunner_utils.script_run_context",
    ):
        logger = logging.getLogger(name)
        logger.addFilter(streamlit_filter)

    # Streamlit's own ``get_logger`` caches loggers in a dict; wrap it so any
    # later-registered logger also picks up the filter.
    import streamlit.logger as st_logger

    original_get_logger = st_logger.get_logger

    def _wrapped_get_logger(name: str) -> logging.Logger:
        logger = original_get_logger(name)
        # Idempotent: ``logging.Logger.addFilter`` allows duplicates, so guard.
        if streamlit_filter not in logger.filters:
            logger.addFilter(streamlit_filter)
        return logger

    st_logger.get_logger = _wrapped_get_logger
    # Re-apply to anything that already registered before we patched.
    for logger in st_logger._loggers.values():
        if streamlit_filter not in logger.filters:
            logger.addFilter(streamlit_filter)


_silence_streamlit_bare_mode_warnings()


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
    analyze,
    balance,
    click_approvals,
    debug_scenarios,
    edit_dsl,
    events,
    gallery,
    gift_codes,
    instances,
    labeling,
    modules,
    optimizer,
    overlay_test,
    overview,
    players,
    queue,
    routes,
    wiki,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _install_asyncio_shutdown_exception_handler()
    yield


app = FastAPI(title="WOS Autopilot API", version="0.1.0", lifespan=_lifespan)

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
app.include_router(queue.router)
app.include_router(events.router)
app.include_router(players.router)
app.include_router(labeling.router)
app.include_router(routes.router)
app.include_router(gift_codes.router)
app.include_router(wiki.router)
app.include_router(click_approvals.router)
app.include_router(overlay_test.router)
app.include_router(modules.router)
app.include_router(gallery.router)
app.include_router(analyze.router)
app.include_router(adb.router)
app.include_router(debug_scenarios.router)
app.include_router(balance.router)
app.include_router(optimizer.router)
app.include_router(edit_dsl.router)


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
            reload=os.environ.get("WOS_API_RELOAD", "").strip().lower() in {"1", "true", "yes"},
            log_level=os.environ.get("WOS_API_LOG_LEVEL", "info"),
            timeout_graceful_shutdown=graceful_shutdown,
        )
    except KeyboardInterrupt:
        # Uvicorn re-raises SIGINT after shutdown; avoid a second traceback on exit.
        sys.exit(0)


if __name__ == "__main__":
    main()
