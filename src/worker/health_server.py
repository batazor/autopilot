"""Minimal localhost HTTP health endpoint for the headless bot supervisor.

The worker process serves no API of its own, so container orchestration has no
HTTP signal to probe — only process liveness. This exposes a tiny stdlib
``/health`` endpoint that reports the supervisor's *loop* liveness (not just
that the process exists), so a hung-but-alive supervisor is caught too.

Bound to loopback only. Failures to bind are non-fatal: the supervisor keeps
running and the (now-unreachable) healthcheck simply trips, which is the
correct signal anyway.
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _make_handler(is_healthy: Callable[[], bool]) -> type[BaseHTTPRequestHandler]:
    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # stdlib dispatch name (do_<METHOD>)
            if self.path.rstrip("/") not in ("/health", ""):
                self.send_error(404)
                return
            try:
                ok = bool(is_healthy())
            except Exception:  # a probe must never raise out of the handler
                logger.debug("health probe callback failed", exc_info=True)
                ok = False
            body = b"ok" if ok else b"stale"
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            # Silence the default per-request stderr logging.
            return

    return _HealthHandler


def start_health_server(
    is_healthy: Callable[[], bool],
    *,
    host: str = "127.0.0.1",
    port: int,
    log: logging.Logger | None = None,
) -> ThreadingHTTPServer | None:
    """Serve ``/health`` in a daemon thread. Returns the server, or ``None``.

    ``is_healthy`` is called per request; truthy → ``200 ok``, else ``503 stale``.
    A bind failure is logged and swallowed (returns ``None``) so the supervisor
    is never taken down by the health endpoint.
    """
    log = log or logger
    try:
        server = ThreadingHTTPServer((host, port), _make_handler(is_healthy))
    except OSError:
        log.warning("bot health server: failed to bind %s:%d", host, port, exc_info=True)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="bot-health", daemon=True).start()
    log.info("bot health server listening on http://%s:%d/health", host, port)
    return server
