"""The bot supervisor's loopback ``/health`` endpoint."""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest

from worker.health_server import start_health_server

if TYPE_CHECKING:
    from collections.abc import Iterator


def _fetch(port: int, path: str = "/health") -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:  # 503 etc. arrive as HTTPError
        return exc.code, exc.read().decode()


@pytest.fixture
def serve() -> Iterator:
    servers = []

    def _start(is_healthy):
        srv = start_health_server(is_healthy, port=0)  # port 0 → OS picks a free one
        assert srv is not None
        servers.append(srv)
        return srv.server_address[1]

    yield _start
    for srv in servers:
        srv.shutdown()


def test_healthy_returns_200_ok(serve) -> None:
    port = serve(lambda: True)
    assert _fetch(port) == (200, "ok")


def test_unhealthy_returns_503_stale(serve) -> None:
    port = serve(lambda: False)
    assert _fetch(port) == (503, "stale")


def test_unknown_path_404(serve) -> None:
    port = serve(lambda: True)
    status, _ = _fetch(port, "/nope")
    assert status == 404


def test_callback_exception_is_treated_as_unhealthy(serve) -> None:
    def _boom() -> bool:
        msg = "probe blew up"
        raise RuntimeError(msg)

    port = serve(_boom)
    assert _fetch(port) == (503, "stale")
