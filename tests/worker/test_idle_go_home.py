"""Idle go-home nudge: an empty queue must park the bot back on main_city.

Live observation (2026-07-06): a finished mail sweep leaves the bot camped on
``mail.starred`` for hours — nothing navigates away once the queue is empty,
while main_city is where detection is most reliable and where most overlay push
rules live.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from worker.instance_worker import (
    _IDLE_GO_HOME_AFTER_S,
    InstanceWorker,
    should_go_home,
)


def test_should_go_home_policy() -> None:
    assert should_go_home("mail.starred") is True
    assert should_go_home("exploration.defeat") is True
    # Already home — both hubs count (marching off main_world would fight
    # the intel/march flows that live there).
    assert should_go_home("main_city") is False
    assert should_go_home("main_world") is False
    # Unknown screen: recovery belongs to the popup/unknown-screen machinery,
    # and the queue's screen-identity gate would park the nav scenario anyway.
    assert should_go_home("") is False
    assert should_go_home("   ") is False


class _FakeRedis:
    def __init__(self, screen: str) -> None:
        self.screen = screen
        self.kv: dict[str, str] = {}

    async def hget(self, key: str, field: str):
        return self.screen.encode() if field == "current_screen" else None

    async def set(self, key: str, value: str, nx: bool = False, ex: int = 0):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


def _worker(screen: str) -> InstanceWorker:
    w = object.__new__(InstanceWorker)
    w._cfg = type("Cfg", (), {"instance_id": "bs6"})()
    w._redis = _FakeRedis(screen)
    w._queue = _FakeQueue()
    w._ui_paused = False
    w._idle_since_m = None
    return w


@pytest.mark.asyncio
async def test_idle_nudge_enqueues_check_main_city_once() -> None:
    w = _worker("mail.starred")
    # First empty pop only arms the idle timer.
    await w._maybe_go_home_when_idle()
    assert w._queue.calls == []

    # Past the idle window → one low-priority check_main_city, throttled after.
    w._idle_since_m = time.monotonic() - _IDLE_GO_HOME_AFTER_S - 1
    await w._maybe_go_home_when_idle()
    await w._maybe_go_home_when_idle()
    assert [c["task_type"] for c in w._queue.calls] == ["check_main_city"]
    call = w._queue.calls[0]
    assert call["skip_if_duplicate"] is True
    assert call["player_id"] == ""


@pytest.mark.asyncio
async def test_idle_nudge_skips_when_home_or_paused() -> None:
    w = _worker("main_city")
    w._idle_since_m = time.monotonic() - _IDLE_GO_HOME_AFTER_S - 1
    await w._maybe_go_home_when_idle()
    assert w._queue.calls == []

    w2 = _worker("mail.starred")
    w2._ui_paused = True
    w2._idle_since_m = time.monotonic() - _IDLE_GO_HOME_AFTER_S - 1
    await w2._maybe_go_home_when_idle()
    assert w2._queue.calls == []


@pytest.mark.asyncio
async def test_idle_nudge_ignores_unknown_screen() -> None:
    w = _worker("")
    w._idle_since_m = time.monotonic() - _IDLE_GO_HOME_AFTER_S - 1
    await w._maybe_go_home_when_idle()
    assert w._queue.calls == []


def test_asyncio_marker_sanity() -> None:
    # Guard against the file silently running no async tests if the plugin
    # configuration changes.
    assert asyncio.iscoroutinefunction(InstanceWorker._maybe_go_home_when_idle)
