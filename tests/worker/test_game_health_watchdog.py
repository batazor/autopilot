from __future__ import annotations

import threading

from worker.game_health_watchdog import _is_game_foreground_after_retries


class _FakeBotActions:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls = 0

    def is_game_foreground(self, _instance_id: str) -> bool:
        self.calls += 1
        if not self._results:
            return False
        return self._results.pop(0)


def test_foreground_retry_recovers_without_restart() -> None:
    ba = _FakeBotActions([False, True])

    assert _is_game_foreground_after_retries(
        ba,
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 2


def test_foreground_retry_fails_after_all_attempts() -> None:
    ba = _FakeBotActions([False, False, False, False])

    assert not _is_game_foreground_after_retries(
        ba,
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 4
