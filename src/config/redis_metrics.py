"""Redis client instrumentation — record one metric sample per command.

Wraps ``execute_command`` on a ``redis.Redis`` / ``redis.asyncio.Redis``
instance so every call increments :func:`config.tracing.redis_command_counter`
and records latency on :func:`config.tracing.redis_command_duration_histogram`.

Why ``execute_command``: every high-level method on the redis-py client
funnels through it, so a single wrap covers ``get``, ``hset``, ``zadd``,
``pipeline.execute``, …. We label by the wire command (``GET`` / ``HSET`` /
``ZADD``) plus a ``component`` tag the caller supplies (``scheduler``,
``worker``, ``ui``, …) so dashboards can split traffic by role.

Idempotent: a marker attribute on the bound wrapper short-circuits a second
wrap, so repeated calls (e.g. lazy ``get_X()`` accessors firing per-request)
don't stack handlers.
"""
from __future__ import annotations

import inspect
import time
from typing import Any

from config.tracing import redis_command_counter, redis_command_duration_histogram

_MARKER = "_wos_metrics_wrapped"


def _command_label(args: tuple[Any, ...]) -> str:
    """First token of the wire command, uppercased — ``CLUSTER NODES`` → ``CLUSTER``."""
    if not args:
        return "UNKNOWN"
    first = args[0]
    if isinstance(first, bytes):
        try:
            first = first.decode("ascii", errors="replace")
        except Exception:
            return "UNKNOWN"
    return str(first).split()[0].upper()


def instrument_redis_client[T](client: T, *, component: str) -> T:
    """Patch ``execute_command`` on ``client`` so each call emits one metric sample.

    ``component`` is stamped onto every emitted point — pick a short label that
    identifies the producer (``scheduler``, ``worker``, ``ui``, ``cli``,
    ``approvals``, …). Returns the same client object for call-site chaining
    (``client = instrument_redis_client(redis.from_url(...), component="ui")``).
    """
    original = getattr(client, "execute_command", None)
    if original is None:
        return client
    if getattr(original, _MARKER, False):
        return client

    counter = redis_command_counter()
    histogram = redis_command_duration_histogram()

    if inspect.iscoroutinefunction(original):
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            cmd = _command_label(args)
            outcome = "ok"
            start = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            except BaseException:
                outcome = "error"
                raise
            finally:
                elapsed = time.perf_counter() - start
                attrs = {"command": cmd, "component": component, "outcome": outcome}
                counter.add(1, attributes=attrs)
                histogram.record(elapsed, attributes=attrs)
    else:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            cmd = _command_label(args)
            outcome = "ok"
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            except BaseException:
                outcome = "error"
                raise
            finally:
                elapsed = time.perf_counter() - start
                attrs = {"command": cmd, "component": component, "outcome": outcome}
                counter.add(1, attributes=attrs)
                histogram.record(elapsed, attributes=attrs)

    setattr(wrapped, _MARKER, True)
    # Instance-level rebind — leaves the class untouched, so other clients in
    # the same process that opted out (or live in tests) keep the vanilla
    # method. Has the side benefit that pickling/cloning a client returns an
    # unwrapped copy, which is the right default.
    client.execute_command = wrapped  # type: ignore[attr-defined]
    return client
