from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

import adb.bot_actions as tap_module
from adb import BotActions
from layout.types import Point
from worker import frame_bus


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


@pytest.fixture(autouse=True)
def _reset_bus() -> Any:
    frame_bus.reset_for_test()
    yield
    frame_bus.reset_for_test()


def _publish(counter: list[int], instance_id: str = "bs1") -> np.ndarray:
    counter[0] += 1
    frame = np.full((10, 10, 3), counter[0], dtype=np.uint8)
    frame_bus.publish(instance_id, frame)
    return frame


def _capture_after_next_publish(
    bot: BotActions, counter: list[int], instance_id: str = "bs1"
) -> np.ndarray:
    """``wait_for_next`` must see a publish that happens after the wait starts."""
    out: list[np.ndarray] = []

    def _worker() -> None:
        out.append(bot.capture_screen_bgr_cached(instance_id))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    time.sleep(0.02)
    _publish(counter, instance_id)
    thread.join(timeout=5.0)
    assert out, "capture timed out waiting for frame_bus publish"
    return out[0]


@pytest.fixture
def actions_with_stub(mocker) -> tuple[BotActions, list[int]]:
    """Real BotActions wired to a stub controller; ``counter`` tracks frame_bus publishes."""
    counter = [0]

    from config.loader import get_settings

    bot = BotActions(get_settings())
    mocker.patch.object(bot, "_get_serial", new=lambda _id: "stub-serial")
    mocker.patch.object(bot, "_adb_bin", new=lambda: "adb")
    mocker.patch.object(bot, "_controller", new=lambda _id: _StubController())

    return bot, counter


def test_cached_capture_skips_adb_until_action(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    _publish(counter, "bs1")

    f1 = bot.capture_screen_bgr_cached("bs1")
    f2 = bot.capture_screen_bgr_cached("bs1")
    f3 = bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1
    assert int(f1[0, 0, 0]) == int(f2[0, 0, 0]) == int(f3[0, 0, 0]) == 1


def test_tap_invalidates_frame_cache(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    _publish(counter, "bs1")

    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1
    bot.tap("bs1", Point(50, 50))
    _capture_after_next_publish(bot, counter, "bs1")
    assert counter[0] == 2


@pytest.mark.parametrize(
    ("action_name", "call"),
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
    _publish(counter, "bs1")
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1
    call(bot)
    _capture_after_next_publish(bot, counter, "bs1")
    assert counter[0] == 2, f"{action_name} should invalidate the cache"


def test_separate_instance_ids_have_separate_caches(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    _publish(counter, "bs1")
    _publish(counter, "bs2")
    bot.capture_screen_bgr_cached("bs1")
    bot.capture_screen_bgr_cached("bs2")
    assert counter[0] == 2
    bot.tap("bs1", Point(0, 0))
    _capture_after_next_publish(bot, counter, "bs1")
    bot.capture_screen_bgr_cached("bs2")
    assert counter[0] == 3


def test_explicit_capture_warms_cache(actions_with_stub: tuple[BotActions, list[int]]) -> None:
    bot, counter = actions_with_stub
    _publish(counter, "bs1")
    bot.capture_screen_bgr("bs1")
    assert counter[0] == 1
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1


def test_max_age_ms_recaptures_when_cache_too_old(
    actions_with_stub: tuple[BotActions, list[int]],
    mocker,
) -> None:
    bot, counter = actions_with_stub
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    mocker.patch.object(tap_module.time, "monotonic", new=_now)

    _publish(counter, "bs1")
    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)
    assert counter[0] == 1

    fake_now[0] += 0.2
    bot.capture_screen_bgr_cached("bs1", max_age_ms=300.0)
    assert counter[0] == 1

    fake_now[0] += 0.2
    _capture_after_next_publish(bot, counter, "bs1")
    assert counter[0] == 2

    fake_now[0] += 60.0
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 2


def test_max_age_ms_does_not_affect_cache_for_no_age_callers(
    actions_with_stub: tuple[BotActions, list[int]],
    mocker,
) -> None:
    bot, counter = actions_with_stub
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    mocker.patch.object(tap_module.time, "monotonic", new=_now)

    _publish(counter, "bs1")
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1

    fake_now[0] += 10.0
    bot.capture_screen_bgr_cached("bs1")
    assert counter[0] == 1

    _capture_after_next_publish(bot, counter, "bs1")
    assert counter[0] == 2
