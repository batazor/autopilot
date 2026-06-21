"""Unit tests for ``config.redis_metrics.instrument_redis_client``.

The wrapper is exercised against a hand-rolled fake client — both a sync
``execute_command`` and an async one — so we don't need real Redis (and don't
need the OTel SDK initialised; the OTel API is a no-op without a provider and
the wrapper still has to handle errors / coroutine detection correctly).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from config.redis_metrics import _command_label, instrument_redis_client


class _SyncFake:
    """Stand-in for ``redis.Redis`` — only ``execute_command`` is exercised."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.raise_on_next = False

    def execute_command(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(args)
        if self.raise_on_next:
            self.raise_on_next = False
            msg = "boom"
            raise RuntimeError(msg)
        return "OK"


class _AsyncFake:
    """Stand-in for ``redis.asyncio.Redis``."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def execute_command(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(args)
        return "OK"


def test_command_label_uppercases_first_token() -> None:
    assert _command_label(("get", "k")) == "GET"
    assert _command_label(("HSET", "h", "f", "v")) == "HSET"
    # Multi-word admin commands collapse to the leading verb so cardinality
    # stays bounded — "CLUSTER NODES" / "CLUSTER SLOTS" both land under CLUSTER.
    assert _command_label(("cluster nodes",)) == "CLUSTER"
    assert _command_label((b"PING",)) == "PING"
    assert _command_label(()) == "UNKNOWN"


def test_sync_wrapper_passes_through_result_and_records() -> None:
    """Successful call returns the underlying result; wrapper does not eat it."""
    fake = _SyncFake()
    instrument_redis_client(fake, component="test")
    out = fake.execute_command("GET", "k")
    assert out == "OK"
    assert fake.calls == [("GET", "k")]


def test_sync_wrapper_preserves_exceptions() -> None:
    fake = _SyncFake()
    instrument_redis_client(fake, component="test")
    fake.raise_on_next = True
    with pytest.raises(RuntimeError, match="boom"):
        fake.execute_command("GET", "k")


def test_async_wrapper_awaits_underlying_coroutine() -> None:
    fake = _AsyncFake()
    instrument_redis_client(fake, component="test")
    out = asyncio.run(fake.execute_command("ZADD", "q", 1, "a"))
    assert out == "OK"
    assert fake.calls == [("ZADD", "q", 1, "a")]


def test_wrapping_is_idempotent() -> None:
    """Two successive ``instrument_redis_client`` calls don't stack wrappers."""
    fake = _SyncFake()
    instrument_redis_client(fake, component="test")
    first = fake.execute_command
    instrument_redis_client(fake, component="test")
    # Same wrapper instance — the marker short-circuits the second pass.
    assert fake.execute_command is first


def test_returns_same_client_for_chaining() -> None:
    fake = _SyncFake()
    assert instrument_redis_client(fake, component="test") is fake
