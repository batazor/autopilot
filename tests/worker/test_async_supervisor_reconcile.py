"""Hot device-reconcile loop: workers track the registry without a restart."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from worker import async_supervisor


def _settings_with(ids: list[str]) -> Any:
    """Minimal stand-in for Settings — only ``instances[*].instance_id`` matters."""
    return SimpleNamespace(
        instances=[SimpleNamespace(instance_id=i) for i in ids],
    )


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the real worker coroutine + settings read with controllable stubs."""
    spawned: list[str] = []

    async def fake_worker(inst: Any, settings: Any) -> None:
        spawned.append(inst.instance_id)
        # Live forever until the reconcile loop cancels us (mirrors the guarded
        # worker, which only exits on cancellation).
        await asyncio.Event().wait()

    state = {"ids": ["bs1"]}
    monkeypatch.setattr(async_supervisor, "_guarded_worker", fake_worker)
    monkeypatch.setattr(
        async_supervisor, "_read_fresh_settings", lambda: _settings_with(state["ids"])
    )
    # Don't pollute the process-wide settings global during the test.
    monkeypatch.setattr(async_supervisor, "set_settings", lambda _s: None)
    return {"spawned": spawned, "state": state}


async def _drain(workers: dict[str, asyncio.Task[None]]) -> None:
    for task in workers.values():
        task.cancel()
    await asyncio.gather(*workers.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_reconcile_spawns_worker_for_new_device(_patched: dict[str, Any]) -> None:
    workers: dict[str, asyncio.Task[None]] = {}

    await async_supervisor._reconcile_once(workers)
    assert set(workers) == {"bs1"}

    _patched["state"]["ids"] = ["bs1", "bs2"]
    await async_supervisor._reconcile_once(workers)
    assert set(workers) == {"bs1", "bs2"}

    # Let the freshly-created worker tasks run their first step so the stub can
    # record that each instance actually started.
    await asyncio.sleep(0)
    assert _patched["spawned"] == ["bs1", "bs2"]

    await _drain(workers)


@pytest.mark.asyncio
async def test_reconcile_cancels_worker_for_removed_device(
    _patched: dict[str, Any],
) -> None:
    workers: dict[str, asyncio.Task[None]] = {}
    _patched["state"]["ids"] = ["bs1", "bs2"]
    await async_supervisor._reconcile_once(workers)
    assert set(workers) == {"bs1", "bs2"}
    removed = workers["bs2"]

    _patched["state"]["ids"] = ["bs1"]
    await async_supervisor._reconcile_once(workers)
    assert set(workers) == {"bs1"}
    assert removed.cancelled()

    await _drain(workers)


@pytest.mark.asyncio
async def test_reconcile_noop_when_unchanged(
    monkeypatch: pytest.MonkeyPatch, _patched: dict[str, Any]
) -> None:
    rebinds: list[Any] = []
    monkeypatch.setattr(async_supervisor, "set_settings", lambda s: rebinds.append(s))

    workers: dict[str, asyncio.Task[None]] = {}
    await async_supervisor._reconcile_once(workers)
    first = dict(workers)

    # Same registry → no rebind, no respawn, same task identities.
    await async_supervisor._reconcile_once(workers)
    assert workers == first
    assert len(rebinds) == 1  # only the initial change rebound settings

    await _drain(workers)
