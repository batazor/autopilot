"""Tests for InstanceWorker._resolve_queue_item_player.

Cases:
- DSL scenario, no active_player → stays device-level (empty player_id)
- DSL scenario, active_player known → gets assigned
- who_i_am (DSL probe), no active_player → stays empty (not pre-empted by config)
- registered task, no active_player → uses devices.yaml player
- registered task, active_player known → uses active_player
- already has player_id → returned as-is
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from types import SimpleNamespace
from typing import Any

import pytest

import worker.instance_worker as instance_worker
from scheduler.queue import QueueItem


class _Redis:
    def __init__(self, active_player: str = "") -> None:
        self._active_player = active_player

    async def hget(self, _key: str, _field: str, **_kw: Any) -> bytes | None:
        return self._active_player.encode() if self._active_player else None


def _item(task_type: str, *, player_id: str = "") -> QueueItem:
    return QueueItem(
        task_id="t1",
        player_id=player_id,
        task_type=task_type,
        priority=1,
        run_at=1.0,
        instance_id="bs1",
    )


def _worker(active_player: str = "") -> Any:
    w = object.__new__(instance_worker.InstanceWorker)
    w._cfg = SimpleNamespace(instance_id="bs1", bluestacks_window_title="emulator-5554")
    w._redis = _Redis(active_player)
    return w


@pytest.mark.asyncio
async def test_dsl_no_active_player_stays_device_level() -> None:
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker(), _item("mail.claim")
    )
    assert resolved.player_id == ""


@pytest.mark.asyncio
async def test_dsl_with_active_player_gets_assigned() -> None:
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker("765502864"), _item("mail.claim")
    )
    assert resolved.player_id == "765502864"


@pytest.mark.asyncio
async def test_who_i_am_without_active_player_stays_empty() -> None:
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker(), _item("who_i_am")
    )
    assert resolved.player_id == ""


@pytest.mark.asyncio
async def test_registered_task_no_active_player_uses_devices_yaml(monkeypatch: Any) -> None:
    class _Reg:
        pass

    monkeypatch.setitem(instance_worker._TASK_REGISTRY, "arena", _Reg)
    # Production code now resolves through ``player_ids_for_device_candidates``
    # (accepts both the bluestacks_window_title and the instance_id as aliases).
    # Monkeypatch *that* hook so the test doesn't depend on db/devices.yaml.
    monkeypatch.setattr(
        instance_worker,
        "player_ids_for_device_candidates",
        lambda *_names: ["999000111"],
    )
    monkeypatch.setattr(
        instance_worker, "player_ids_for_device", lambda _name: ["999000111"]
    )
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker(), _item("arena")
    )
    assert resolved.player_id == "999000111"


@pytest.mark.asyncio
async def test_registered_task_active_player_takes_priority(monkeypatch: Any) -> None:
    class _Reg:
        pass

    monkeypatch.setitem(instance_worker._TASK_REGISTRY, "arena", _Reg)
    monkeypatch.setattr(
        instance_worker,
        "player_ids_for_device_candidates",
        lambda *_names: ["999000111"],
    )
    monkeypatch.setattr(
        instance_worker, "player_ids_for_device", lambda _name: ["999000111"]
    )
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker("765502864"), _item("arena")
    )
    assert resolved.player_id == "765502864"


@pytest.mark.asyncio
async def test_item_with_player_id_returned_unchanged() -> None:
    item = _item("mail.claim", player_id="111222333")
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker("999000000"), item
    )
    assert resolved.player_id == "111222333"


@pytest.mark.asyncio
async def test_resolve_preserves_ranking_fields() -> None:
    """Resolve must carry ``created_at`` + ``effective_priority`` through.

    ``effective_priority`` is the rank-time score that DslScenarioTask uses for
    preemption comparisons (``instance_worker.py:167``); ``created_at`` is the
    stable tie-breaker (``scheduler/queue.py:98``). A naive field-by-field
    QueueItem rebuild dropped both, so a resolved device-level task got compared
    by raw ``priority`` against ranked competitors — losing preemption it should
    have won. Lock the invariant: every non-player field passes through verbatim.
    """
    item = QueueItem(
        task_id="t1",
        player_id="",
        task_type="mail.claim",
        priority=10,
        run_at=1.0,
        instance_id="bs1",
        created_at=1234567.5,
        effective_priority=42_000,
    )
    resolved = await instance_worker.InstanceWorker._resolve_queue_item_player(
        _worker("765502864"), item
    )
    assert resolved.player_id == "765502864"
    assert resolved.created_at == 1234567.5
    assert resolved.effective_priority == 42_000
    # Every other field also survives — guards against future field additions
    # silently regressing back to a manual copy.
    for f in dataclass_fields(QueueItem):
        if f.name == "player_id":
            continue
        assert getattr(resolved, f.name) == getattr(item, f.name), (
            f"field {f.name} not preserved across resolve"
        )
