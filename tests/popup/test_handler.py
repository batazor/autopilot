"""Tests for the decoupled loop-until-clear pop-up handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from layout.types import Point, Region
from popup.handler import HandlerConfig, PopupBlockingHandler, PopupHandleResult
from popup.models import DetectionSignals, PopupKind, PopupState

if TYPE_CHECKING:
    from collections.abc import Sequence

_BBOX = Region(100, 300, 460, 480)
_CLOSE = Point(540, 320)
_CLAIM = Point(330, 700)


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
    """Returns a scripted sequence of states, one per detect() call."""

    def __init__(self, states: Sequence[PopupState]) -> None:
        self._states = list(states)
        self.calls = 0

    async def detect(self, image: np.ndarray) -> PopupState:
        state = self._states[min(self.calls, len(self._states) - 1)]
        self.calls += 1
        return state


class _FakeActions:
    def __init__(self) -> None:
        self.taps: list[tuple[str, Point]] = []

    def tap(self, instance_id: str, point: Point) -> bool:
        self.taps.append((instance_id, point))
        return True


def _capture(_instance_id: str) -> np.ndarray:
    return np.zeros((1280, 720, 3), dtype=np.uint8)


_FAST = HandlerConfig(max_layers=4, settle_s=0.0)


async def test_dismisses_stacked_popups_until_clear() -> None:
    detector = _FakeDetector([_state(PopupKind.SAFE_DISMISS), _state(PopupKind.SAFE_DISMISS), _state(PopupKind.NONE)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.HANDLED
    assert actions.taps == [("bs1", _CLOSE), ("bs1", _CLOSE)]


async def test_reward_claim_taps_primary_not_close() -> None:
    detector = _FakeDetector([_state(PopupKind.REWARD_CLAIM, close=None, primary=_CLAIM), _state(PopupKind.NONE)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.HANDLED
    assert actions.taps == [("bs1", _CLAIM)]


async def test_tap_to_continue_taps_center_not_close() -> None:
    # "Tap anywhere" page carries a geometric close_point too, but the handler
    # must tap the center primary_point, not the (non-existent) top-right X.
    center = Point(330, 540)
    detector = _FakeDetector([_state(PopupKind.TAP_TO_CONTINUE, close=_CLOSE, primary=center), _state(PopupKind.NONE)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.HANDLED
    assert actions.taps == [("bs1", center)]


async def test_purchase_taps_only_close() -> None:
    detector = _FakeDetector([_state(PopupKind.PURCHASE), _state(PopupKind.NONE)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.HANDLED
    assert actions.taps == [("bs1", _CLOSE)]


async def test_purchase_without_close_escalates() -> None:
    detector = _FakeDetector([_state(PopupKind.PURCHASE, close=None)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.ESCALATE
    assert actions.taps == []  # never guesses a tap on a purchase modal


async def test_captcha_routes_and_never_taps() -> None:
    routed: list[str] = []

    async def captcha_handler(instance_id: str) -> bool:
        routed.append(instance_id)
        return True

    detector = _FakeDetector([_state(PopupKind.CAPTCHA)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST, captcha_handler=captcha_handler)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.CAPTCHA_ROUTED
    assert routed == ["bs1"]
    assert actions.taps == []


async def test_captcha_without_handler_escalates() -> None:
    detector = _FakeDetector([_state(PopupKind.CAPTCHA)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.ESCALATE
    assert actions.taps == []


async def test_ad_webview_without_close_escalates() -> None:
    detector = _FakeDetector([_state(PopupKind.AD_WEBVIEW, close=None)])
    handler = PopupBlockingHandler(detector, _capture, config=_FAST)
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.ESCALATE
    assert actions.taps == []


async def test_persistent_popup_escalates_after_max_layers() -> None:
    detector = _FakeDetector([_state(PopupKind.SAFE_DISMISS)])  # never clears
    handler = PopupBlockingHandler(detector, _capture, config=HandlerConfig(max_layers=3, settle_s=0.0))
    actions = _FakeActions()

    result = await handler.handle("bs1", actions)

    assert result == PopupHandleResult.ESCALATE
    assert len(actions.taps) == 3  # one tap per layer, then give up
