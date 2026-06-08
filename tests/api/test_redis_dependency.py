"""API Redis dependency wiring."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api import deps


def _build_client(monkeypatch: Any, url: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_from_url(redis_url: str, **kwargs: Any) -> object:
        captured["url"] = redis_url
        captured["kwargs"] = kwargs
        return object()

    def fake_redis(*, connection_pool: object) -> object:
        del connection_pool
        return object()

    def fake_instrument(client: object, *, component: str) -> object:
        del component
        return client

    monkeypatch.setattr(deps, "_redis_client", None)
    monkeypatch.setattr(
        deps,
        "load_settings",
        lambda: SimpleNamespace(redis=SimpleNamespace(url=url)),
    )
    monkeypatch.setattr(
        deps.redis.BlockingConnectionPool,
        "from_url",
        fake_from_url,
    )
    monkeypatch.setattr(deps.redis, "Redis", fake_redis)
    monkeypatch.setattr(deps, "instrument_redis_client", fake_instrument)

    deps.get_redis()
    return captured


def test_api_redis_pool_omits_tcp_keepalive_for_unix_socket(monkeypatch: Any) -> None:
    captured = _build_client(monkeypatch, "unix:///var/run/redis/redis.sock?db=0")

    assert captured["url"] == "unix:///var/run/redis/redis.sock?db=0"
    assert "socket_keepalive" not in captured["kwargs"]


def test_api_redis_pool_keeps_tcp_keepalive_for_tcp_url(monkeypatch: Any) -> None:
    captured = _build_client(monkeypatch, "redis://redis:6379/0")

    assert captured["url"] == "redis://redis:6379/0"
    assert captured["kwargs"]["socket_keepalive"] is True
