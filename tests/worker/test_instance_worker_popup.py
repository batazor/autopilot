"""Unit tests for ``InstanceWorker._maybe_handle_popup``.

Hermetic: no Redis, no emulator. A fake detector scripts the ``PopupState``;
a fake ``BotActions`` records taps; ``_run_rolling_blocking`` is overridden to
call inline. Cooldown uses the in-process monotonic guard (``_redis = None``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np

from layout.types import Point, Region
from popup.models import DetectionSignals, PopupKind, PopupState
from worker.instance_worker import InstanceWorker

if TYPE_CHECKING:
    from collections.abc import Callable

_CLOSE = Point(540, 320)
_CLAIM = Point(330, 700)
_BBOX = Region(100, 300, 460, 480)


def _state(kind: PopupKind, *, close: Point | None = _CLOSE, primary: Point | None = None) -> PopupState:
    overlay = kind != PopupKind.NONE
    return PopupState(
        kind=kind,
        bbox=None if kind == PopupKind.NONE else _BBOX,
        close_point=close,
        primary_point=primary,
        card_text="",
        signals=DetectionSignals(card_frac=0.5, center=(0.5, 0.5), scrim_sharp=0.0, overlay_present=overlay),
    )


class _FakeDetector:
    def __init__(self, state: PopupState) -> None:
        self._state = state
        self.calls = 0

    async def detect(self, _image: np.ndarray) -> PopupState:
        self.calls += 1
        return self._state


class _FakeActions:
    def __init__(self) -> None:
        self.taps: list[tuple[str, Point, dict]] = []

    def tap(self, instance_id: str, point: Point, **kwargs: object) -> bool:
        self.taps.append((instance_id, point, dict(kwargs)))
        return True


def _worker(detector: _FakeDetector, actions: _FakeActions, *, enabled: bool = True, busy: bool = False) -> InstanceWorker:
    w = object.__new__(InstanceWorker)
    w._cfg = SimpleNamespace(instance_id="bs1")
    w._settings = SimpleNamespace(worker=SimpleNamespace(popup_detector_enabled=enabled))
    w._popup_detector = detector
    w._bot_actions = actions
    w._redis = None
    w._last_popup_tap_mono = 0.0
    busy_event = asyncio.Event()
    if busy:
        busy_event.set()
    w._task_busy = busy_event

    async def _run_inline(fn: Callable[..., object], /, *args: object, **kwargs: object) -> object:
        return fn(*args, **kwargs)

    w._run_rolling_blocking = _run_inline  # type: ignore[method-assign]
    return w


_FRAME = np.zeros((1280, 720, 3), dtype=np.uint8)


async def test_disabled_flag_is_noop() -> None:
    detector = _FakeDetector(_state(PopupKind.SAFE_DISMISS))
    actions = _FakeActions()
    worker = _worker(detector, actions, enabled=False)

    assert await worker._maybe_handle_popup(_FRAME) is False
    assert detector.calls == 0  # never even runs the detector
    assert actions.taps == []


async def test_busy_task_defers() -> None:
    detector = _FakeDetector(_state(PopupKind.SAFE_DISMISS))
    actions = _FakeActions()
    worker = _worker(detector, actions, busy=True)

    assert await worker._maybe_handle_popup(_FRAME) is False
    assert actions.taps == []


async def test_none_returns_false() -> None:
    detector = _FakeDetector(_state(PopupKind.NONE))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    assert await worker._maybe_handle_popup(_FRAME) is False
    assert actions.taps == []


async def test_safe_dismiss_taps_close() -> None:
    detector = _FakeDetector(_state(PopupKind.SAFE_DISMISS))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    assert await worker._maybe_handle_popup(_FRAME) is True
    assert len(actions.taps) == 1
    instance_id, point, kwargs = actions.taps[0]
    assert instance_id == "bs1"
    assert point == _CLOSE
    assert kwargs["approval_region"] == "popup_close"
    assert kwargs["approval_source"] == "popup"


async def test_reward_taps_primary_not_close() -> None:
    detector = _FakeDetector(_state(PopupKind.REWARD_CLAIM, close=None, primary=_CLAIM))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    assert await worker._maybe_handle_popup(_FRAME) is True
    assert [t[1] for t in actions.taps] == [_CLAIM]


async def test_purchase_taps_only_close() -> None:
    detector = _FakeDetector(_state(PopupKind.PURCHASE))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    assert await worker._maybe_handle_popup(_FRAME) is True
    assert [t[1] for t in actions.taps] == [_CLOSE]


async def test_captcha_never_taps_but_handles() -> None:
    detector = _FakeDetector(_state(PopupKind.CAPTCHA))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    # Returns True so the shotgun fallback can't blindly tap into the captcha.
    assert await worker._maybe_handle_popup(_FRAME) is True
    assert actions.taps == []


async def test_ad_webview_without_close_defers_to_shotgun() -> None:
    detector = _FakeDetector(_state(PopupKind.AD_WEBVIEW, close=None))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    # No actionable point → False so the normal pipeline + shotgun still run.
    assert await worker._maybe_handle_popup(_FRAME) is False
    assert actions.taps == []


async def test_cooldown_suppresses_immediate_retap() -> None:
    detector = _FakeDetector(_state(PopupKind.SAFE_DISMISS))
    actions = _FakeActions()
    worker = _worker(detector, actions)

    first = await worker._maybe_handle_popup(_FRAME)
    second = await worker._maybe_handle_popup(_FRAME)

    assert first is True
    assert second is True  # still handled (modal present), but no new tap
    assert len(actions.taps) == 1  # cooldown blocked the second tap
