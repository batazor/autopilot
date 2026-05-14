from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import actions.tap as tap_module
from actions.tap import BotActions, Point


class _StubController:
    def __init__(self) -> None:
        self.taps: list[Point] = []
        self.swipes: int = 0

    def tap(self, point: Point, **_: Any) -> bool:
        self.taps.append(point)
        return True

    def swipe(self, *_a: Any, **_kw: Any) -> bool:
        self.swipes += 1
        return True

    def swipe_direction(self, *_a: Any, **_kw: Any) -> bool:
        self.swipes += 1
        return True

    def long_tap(self, *_a: Any, **_kw: Any) -> bool:
        return True

    def type_text(self, *_a: Any) -> bool:
        return True

    def restart_application(self) -> None: ...

    def ensure_game_foreground(self) -> None: ...

    def get_screen_resolution(self) -> tuple[int, int]:
        return 720, 1280

    def attach_approval_preview(self, *_a: Any, **_kw: Any) -> None: ...


@pytest.fixture()
def actions_with_stub(monkeypatch: pytest.MonkeyPatch) -> tuple[BotActions, list[int]]:
    """Real BotActions wired to a stub controller and counted screencap."""
    counter = [0]

    def _fake_screencap(_bin: str, _serial: str) -> tuple[np.ndarray, str | None]:
        counter[0] += 1
        # Encode the counter into the frame so callers can detect a fresh capture.
        frame = np.full((10, 10, 3), counter[0], dtype=np.uint8)
        return frame, None

    monkeypatch.setattr(tap_module, "adb_screencap_bgr", _fake_screencap)

    bot = BotActions()
    monkeypatch.setattr(bot, "_get_serial", lambda _id: "stub-serial")
    monkeypatch.setattr(bot, "_adb_bin", lambda: "adb")
    monkeypatch.setattr(bot, "_controller", lambda _id: _StubController())

    return bot, counter


def test_cached_capture_skips_adb_until_action(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub

    f1 = bot.capture_screen_bgr_cached("bs1")
    f2 = bot.capture_screen_bgr_cached("bs1")
    f3 = bot.capture_screen_bgr_cached("bs1")
    # Three cached calls in a row → one ADB screencap.
    assert counter[0] == 1
    assert int(f1[0, 0, 0]) == int(f2[0, 0, 0]) == int(f3[0, 0, 0]) == 1


def test_tap_invalidates_frame_cache(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub

    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1
    bot.tap("bs1", Point(50, 50))
    bot.capture_screen_bgr_cached("bs1")
    # Tap invalidated → next cached call must hit ADB again.
    assert counter[0] == 2


@pytest.mark.parametrize(
    "action_name,call",
    [
        ("swipe", lambda b: b.swipe("bs1", Point(0, 0), Point(10, 10))),
        ("swipe_direction", lambda b: b.swipe_direction("bs1", "up", 100)),
        ("long_tap", lambda b: b.long_tap("bs1", Point(5, 5))),
        ("type_text", lambda b: b.type_text("bs1", "abc")),
        ("ensure_game_foreground", lambda b: b.ensure_game_foreground("bs1")),
    ],
)
def test_state_changing_actions_invalidate(
    actions_with_stub: tuple[BotActions, list[int]],
    action_name: str,
    call: Any,
) -> None:
    bot, counter = actions_with_stub
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1
    call(bot)
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 2, f"{action_name} should invalidate the cache"


def test_separate_instance_ids_have_separate_caches(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    bot.capture_screen_bgr_cached("bs1")
    bot.capture_screen_bgr_cached("bs2")
    assert counter[0] == 2
    bot.tap("bs1", Point(0, 0))
    bot.capture_screen_bgr_cached("bs1")
    bot.capture_screen_bgr_cached("bs2")  # bs2 cache survived bs1's tap
    assert counter[0] == 3


def test_explicit_capture_warms_cache(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    bot.capture_screen_bgr("bs1")
    assert counter[0] == 1
    # Immediate cached call returns the same frame without re-capturing.
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1


def test_max_age_ms_recaptures_when_cache_too_old(
    actions_with_stub: tuple[BotActions, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_age_ms`` invalidates the cached frame when it's older than the gate.

    OCR reads of countdowns/timers opt into this so a 300-ms-old frame doesn't
    serve a stale seconds value. ``None`` (default) keeps the old
    tap-invalidation-only behavior — that path is covered by the other tests.
    """
    bot, counter = actions_with_stub
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr(tap_module.time, "monotonic", _now)

    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)
    assert counter[0] == 1

    # 200 ms later — within the gate, no fresh capture.
    fake_now[0] += 0.2
    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)
    assert counter[0] == 1

    # 400 ms past the original capture — beyond the gate, re-capture.
    fake_now[0] += 0.2
    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)
    assert counter[0] == 2

    # A caller without ``max_age_ms`` still serves the freshly-cached frame.
    fake_now[0] += 60.0
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 2


def test_max_age_ms_does_not_affect_cache_for_no_age_callers(
    actions_with_stub: tuple[BotActions, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aging is per-call, not stored on the cache entry.

    A ``match`` caller (no ``max_age_ms``) and a later ``ocr`` caller
    (``max_age_ms=300``) share the same cache key — the age check is applied
    at read time so the ``match`` call must keep working even when the entry
    has aged out of an OCR caller's gate.
    """
    bot, counter = actions_with_stub
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr(tap_module.time, "monotonic", _now)

    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1

    fake_now[0] += 10.0  # well past any OCR-style max_age
    bot.capture_screen_bgr_cached("bs1")  # match-style call, no gate
    assert counter[0] == 1

    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)  # ocr-style call
    assert counter[0] == 2
