from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

import adb.bot_actions as tap_module
from adb import BotActions
from adb.frame_normalize import FrameNormalizeTransform
from layout.types import Point
from worker import frame_bus


class _StubController:
    def __init__(self) -> None:
        self.taps: list[Point] = []
        self.tap_kwargs: list[dict[str, Any]] = []
        self.swipes: int = 0
        self.swipe_points: list[tuple[Point, Point]] = []
        self.resolution: tuple[int, int] = (720, 1280)

    def tap(self, point: Point, **_: Any) -> bool:
        self.taps.append(point)
        self.tap_kwargs.append(dict(_))
        return True

    def swipe(self, start: Point, end: Point, *_a: Any, **_kw: Any) -> bool:
        self.swipes += 1
        self.swipe_points.append((start, end))
        return True

    def swipe_direction(self, *_a: Any, **_kw: Any) -> bool:
        self.swipes += 1
        return True

    def long_tap(self, *_a: Any, **_kw: Any) -> bool:
        return True

    def type_text(self, *_a: Any) -> bool:
        return True

    def restart_application(self, game: str | None = None) -> bool:
        return True

    def ensure_game_foreground(self, game: str | None = None, **_kw: Any) -> bool:
        return True

    def get_screen_resolution(self) -> tuple[int, int]:
        return self.resolution

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

    from config.loader import InstanceConfig, get_settings

    settings = get_settings()
    instances = [
        replace(inst, screenshot_backend="", input_backend="")
        for inst in settings.instances
    ]
    # The tests address instances "bs1" and "bs2" directly; synthesize any the
    # local device DB doesn't provide so the suite runs without a configured
    # emulator (a fresh checkout / CI box has zero rows in state.db).
    template = (
        instances[0]
        if instances
        else InstanceConfig(instance_id="bs1", bluestacks_window_title="stub-serial")
    )
    for required in ("bs1", "bs2"):
        if not any(inst.instance_id == required for inst in instances):
            instances.append(
                replace(
                    template,
                    instance_id=required,
                    screenshot_backend="",
                    input_backend="",
                )
            )
    settings = replace(settings, instances=instances)
    bot = BotActions(settings)
    stub = _StubController()
    mocker.patch.object(bot, "_get_serial", new=lambda _id: "stub-serial")
    mocker.patch.object(bot, "_adb_bin", new=lambda: "adb")
    mocker.patch.object(bot, "_controller", new=lambda _id: stub)
    bot._test_stub_controller = stub  # type: ignore[attr-defined]

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


def test_tap_maps_bot_frame_point_through_cover_cropped_adb_screen(
    actions_with_stub: tuple[BotActions, list[int]],
) -> None:
    bot, _counter = actions_with_stub
    stub = bot._test_stub_controller  # type: ignore[attr-defined]
    stub.resolution = (720, 1600)

    assert bot.tap("bs1", Point(360, 640))

    assert stub.taps == [Point(360, 752)]
    assert stub.tap_kwargs[0]["preview_point"] == Point(360, 640)


def test_tap_uses_latest_frame_transform_for_letterboxed_adb_screen(
    actions_with_stub: tuple[BotActions, list[int]],
) -> None:
    bot, _counter = actions_with_stub
    stub = bot._test_stub_controller  # type: ignore[attr-defined]
    stub.resolution = (720, 1600)
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    transform = FrameNormalizeTransform(
        source_size=(800, 1600),
        target_size=(720, 1280),
        crop_left=40,
        crop_top=220,
        crop_size=(720, 1250),
        scale_x=1.0,
        scale_y=1280 / 1250,
    )
    frame_bus.publish("bs1", frame, transform=transform)
    bot.capture_screen_bgr_cached("bs1")

    assert bot.tap("bs1", Point(360, 640))

    assert stub.taps == [Point(400, 845)]
    assert stub.tap_kwargs[0]["preview_point"] == Point(360, 640)


def test_swipe_direction_maps_delta_through_bot_frame(
    actions_with_stub: tuple[BotActions, list[int]],
) -> None:
    bot, _counter = actions_with_stub
    stub = bot._test_stub_controller  # type: ignore[attr-defined]
    stub.resolution = (1080, 1920)

    with patch("adb.bot_actions.random.uniform", side_effect=[0.5, 0.68]):
        assert bot.swipe_direction("bs1", "up", 100)

    assert stub.swipe_points == [(Point(540, 1305), Point(540, 1155))]


@pytest.mark.parametrize(
    ("action_name", "call"),
    [
        ("swipe", lambda b: b.swipe("bs1", Point(0, 0), Point(10, 10))),
        ("swipe_direction", lambda b: b.swipe_direction("bs1", "up", 100)),
        ("long_tap", lambda b: b.long_tap("bs1", Point(5, 5))),
        ("type_text", lambda b: b.type_text("bs1", "abc")),
        ("restart_application", lambda b: b.restart_application("bs1")),
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


def test_rolling_capture_keeps_pending_settle_boundary(
    actions_with_stub: tuple[BotActions, list[int]],
) -> None:
    """A frame captured mid-animation must not clear a tap's settle boundary."""
    bot, _counter = actions_with_stub
    with bot._frame_cache_lock:
        # Tap set a settle deadline at t=20; a rolling frame grabbed at t=10
        # (before the deadline) must leave the boundary intact.
        bot._await_next_frame["bs1"] = 20.0
        bot._clear_settle_boundary_locked("bs1", 10.0)
        assert bot._await_next_frame.get("bs1") == 20.0
        # A frame captured at/after the deadline clears it.
        bot._clear_settle_boundary_locked("bs1", 25.0)
        assert "bs1" not in bot._await_next_frame


def test_cached_capture_rejects_frame_older_than_settle_boundary(
    actions_with_stub: tuple[BotActions, list[int]],
    mocker,
) -> None:
    """Cached read must not return a frame captured before a pending boundary.

    Reproduces the double-click race: a rolling tick repopulates the cache with
    a pre-tap frame; without honoring the settle boundary the next DSL match
    would re-read that stale frame and click the same button again.
    """
    bot, counter = actions_with_stub
    fake_now = [1000.0]
    mocker.patch.object(tap_module.time, "monotonic", new=lambda: fake_now[0])

    stale = np.full((10, 10, 3), 7, dtype=np.uint8)
    with bot._frame_cache_lock:
        # Cache holds a frame captured at t=1000; a tap then set a settle
        # boundary slightly in the future.
        bot._frame_cache["bs1"] = (1000.0, stale, None)
        bot._await_next_frame["bs1"] = 1000.2

    out = _capture_after_next_publish(bot, counter, "bs1")
    assert int(out[0, 0, 0]) != 7, "stale pre-boundary frame must be rejected"
    assert counter[0] == 1


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
