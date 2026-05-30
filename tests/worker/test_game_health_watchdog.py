from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

from worker.game_health_watchdog import _is_game_running_after_retries

if TYPE_CHECKING:
    from adb import BotActions


class _FakeBotActions:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls = 0

    def is_game_running(self, _instance_id: str) -> bool:
        self.calls += 1
        if not self._results:
            return False
        return self._results.pop(0)


def test_process_retry_recovers_without_restart() -> None:
    # Transient pidof miss then alive → no restart (BlueStacks foreground parse
    # is no longer the criterion; process aliveness is).
    ba = _FakeBotActions([False, True])

    assert _is_game_running_after_retries(
        cast("BotActions", ba),
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 2


def test_process_retry_fails_after_all_attempts() -> None:
    # Process genuinely dead across all attempts → restart escalates.
    ba = _FakeBotActions([False, False, False, False])

    assert not _is_game_running_after_retries(
        cast("BotActions", ba),
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 4
