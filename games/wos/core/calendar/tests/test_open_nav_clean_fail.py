"""``open_calendar_tab`` must not strand the bot on the Events panel.

When the Calendar tab can't be verified (e.g. an unattended click-approval
swallowed a tap), the walk backs the panel out with a system-back and returns
False, so navigation fails to a known screen (main_city) instead of leaving
``currentNode == unknown`` — which would otherwise spin the
``dismiss_unknown_popup`` recovery loop. See
``games/wos/core/calendar/open_nav.py``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from games.wos.core.calendar import open_nav

import navigation.detector as detector_mod


class _FakeDetector:
    """Returns a fixed (or tap-gated) screen name from ``detect_screen``."""

    def __init__(self, *, reach_calendar: bool, actions: _FakeActions) -> None:
        self._reach = reach_calendar
        self._actions = actions

    async def detect_screen(self, _frame: Any, *, expected: str | None = None) -> str:
        # Calendar is only "reached" after its tab has been tapped, never during
        # the swipe loop — so the final post-tap verify is what's exercised.
        if self._reach and self._actions.calendar_tapped:
            return "event.calendar"
        return "unknown"


class _FakeActions:
    def __init__(self) -> None:
        self.regions: list[str] = []
        self.system_back_calls = 0
        self.calendar_tapped = False
        self._frame = np.zeros((1280, 720, 3), dtype=np.uint8)

    def capture_screen_bgr(self, _instance_id: str) -> Any:
        return self._frame  # identical frame each call → strip "stops" fast

    def tap(self, _instance_id: str, _point: Any, **kwargs: Any) -> bool:
        region = kwargs.get("approval_region")
        self.regions.append(region)
        if region == "calendar.tab":
            self.calendar_tapped = True
        return True

    def swipe(self, *_a: Any, **_k: Any) -> bool:
        return True

    def system_back(self, _instance_id: str) -> bool:
        self.system_back_calls += 1
        return True


def _patch(monkeypatch: Any, actions: _FakeActions, *, reach: bool) -> None:
    monkeypatch.setattr(
        detector_mod,
        "ScreenDetector",
        lambda _ocr: _FakeDetector(reach_calendar=reach, actions=actions),
    )


async def test_returns_true_when_calendar_verified(monkeypatch: Any) -> None:
    actions = _FakeActions()
    _patch(monkeypatch, actions, reach=True)
    ok = await open_nav.open_calendar_tab(actions, "bs1", ocr=None)
    assert ok is True
    assert "calendar.tab" in actions.regions
    assert actions.system_back_calls == 0


async def test_backs_out_and_returns_false_when_not_verified(monkeypatch: Any) -> None:
    actions = _FakeActions()
    _patch(monkeypatch, actions, reach=False)
    ok = await open_nav.open_calendar_tab(actions, "bs1", ocr=None)
    assert ok is False
    # Tapped the tab, failed to verify, then backed out of the Events panel.
    assert "calendar.tab" in actions.regions
    assert actions.system_back_calls == 1
