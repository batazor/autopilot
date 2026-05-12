"""Regression: pending click-approval slot must be reaped at worker boot.

The single per-instance approval slot
(``wos:ui:click_approval:current:<instance_id>``) survives worker restarts.
Without an explicit boot-time cleanup, a fresh worker would block on the
previous session's approval — whose owning task is gone, whose context
(``player_id``, scenario state) the new worker has no record of. The
operator's only recovery is manually deleting the key from Redis.

This was hit in practice after a bot restart: ``active_player`` empty,
reconnect-button on screen, but the worker sat on a 13-minute-old swipe
approval published by a pre-restart task.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import worker.instance_worker as instance_worker


@pytest.mark.asyncio
async def test_clear_pending_approval_on_boot_deletes_slot(
    redis_async: object,
) -> None:
    r = redis_async
    key = "wos:ui:click_approval:current:bs1"
    await r.set(key, '{"status":"waiting"}')  # type: ignore[attr-defined]

    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = r

    await instance_worker.InstanceWorker._clear_pending_approval_on_boot(worker)

    assert await r.get(key) is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_clear_pending_approval_on_boot_no_op_when_empty(
    redis_async: object,
) -> None:
    """No pending slot → no errors, no harm."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = redis_async

    # Should not raise.
    await instance_worker.InstanceWorker._clear_pending_approval_on_boot(worker)


@pytest.mark.asyncio
async def test_clear_pending_approval_on_boot_no_op_when_redis_unset(
    redis_async: object,
) -> None:
    """``self._redis is None`` early in setup → cleanup is a no-op."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = None

    await instance_worker.InstanceWorker._clear_pending_approval_on_boot(worker)


@pytest.mark.asyncio
async def test_clear_pending_approval_on_boot_only_touches_own_instance(
    redis_async: object,
) -> None:
    """Each worker owns its own per-instance slot — cleanup must scope to
    ``self._cfg.instance_id`` and leave other instances untouched."""
    r = redis_async
    await r.set("wos:ui:click_approval:current:bs1", '{"status":"waiting"}')  # type: ignore[attr-defined]
    await r.set("wos:ui:click_approval:current:bs2", '{"status":"waiting"}')  # type: ignore[attr-defined]

    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = r

    await instance_worker.InstanceWorker._clear_pending_approval_on_boot(worker)

    assert await r.get("wos:ui:click_approval:current:bs1") is None  # type: ignore[attr-defined]
    assert await r.get("wos:ui:click_approval:current:bs2") is not None  # type: ignore[attr-defined]
