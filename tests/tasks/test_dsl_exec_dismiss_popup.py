from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from conftest import make_actions

import tasks.dsl_exec as dsl_exec
from layout.types import Point, Region
from popup.models import DetectionSignals, PopupKind, PopupState
from tasks import dsl_runtime


def _signals(*, overlay_present: bool = True) -> DetectionSignals:
    return DetectionSignals(
        card_frac=0.4,
        center=(0.5, 0.5),
        scrim_sharp=0.05,
        overlay_present=overlay_present,
    )


def _state(
    kind: PopupKind,
    *,
    close: Point | None = None,
    primary: Point | None = None,
) -> PopupState:
    return PopupState(
        kind=kind,
        bbox=Region(100, 200, 400, 600),
        close_point=close,
        primary_point=primary,
        card_text="",
        signals=_signals(overlay_present=kind != PopupKind.NONE),
    )


class _FakeDetector:
    """Yields a queued sequence of states, one per ``detect`` call."""

    def __init__(self, states: list[PopupState]) -> None:
        self._states = iter(states)
        self.calls = 0

    async def detect(self, _image: Any) -> PopupState:
        self.calls += 1
        return next(self._states, _state(PopupKind.NONE))


def _recording_actions() -> Any:
    taps: list[tuple[int, int, Any]] = []
    actions = make_actions(resolution=(720, 1280))
    actions.capture_screen_bgr.return_value = np.zeros((1280, 720, 3), dtype=np.uint8)

    def _tap(_instance_id: str, point: Any, **kwargs: object) -> bool:
        taps.append((point.x, point.y, kwargs.get("approval_region")))
        return True

    actions.tap.side_effect = _tap
    actions._test_taps = taps  # type: ignore[attr-defined]
    return actions


def _wire(mocker, actions: Any, detector: _FakeDetector, *, enabled: bool = True) -> None:
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(dsl_runtime, "ocr_client", return_value=object())
    mocker.patch.object(
        dsl_runtime,
        "settings",
        return_value=SimpleNamespace(
            worker=SimpleNamespace(popup_detector_enabled=enabled)
        ),
    )
    mocker.patch.object(dsl_exec, "PopupDetector", lambda _ocr: detector)
    mocker.patch.object(dsl_exec, "_DISMISS_POPUP_SETTLE_S", 0)


def _ctx(args: dict[str, Any] | None = None) -> dsl_exec.DslExecContext:
    return dsl_exec.DslExecContext(
        redis_client=None,
        player_id="",
        instance_id="bs1",
        args=args or {},
    )


@pytest.mark.asyncio
async def test_dismiss_popup_disabled_is_noop(mocker, ) -> None:
    """With the detector flag off, the handler defers to the region shotgun."""
    actions = _recording_actions()
    detector = _FakeDetector([_state(PopupKind.SAFE_DISMISS, close=Point(480, 210))])
    _wire(mocker, actions, detector, enabled=False)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert detector.calls == 0
    assert actions._test_taps == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dismiss_popup_taps_close_then_clears(mocker, ) -> None:
    """SAFE_DISMISS → tap the X; loop ends as soon as a frame reads NONE."""
    actions = _recording_actions()
    detector = _FakeDetector(
        [
            _state(PopupKind.SAFE_DISMISS, close=Point(480, 210)),
            _state(PopupKind.NONE),
        ]
    )
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert actions._test_taps == [(480, 210, "popup_close")]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dismiss_popup_stacked_layers(mocker, ) -> None:
    """Reward claim then a tap-to-continue page, each tapped at its CTA."""
    actions = _recording_actions()
    detector = _FakeDetector(
        [
            _state(PopupKind.REWARD_CLAIM, close=Point(480, 210), primary=Point(300, 700)),
            _state(PopupKind.TAP_TO_CONTINUE, primary=Point(360, 500)),
            _state(PopupKind.NONE),
        ]
    )
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert actions._test_taps == [  # type: ignore[attr-defined]
        (300, 700, "popup_close"),
        (360, 500, "popup_tap_anywhere"),
    ]


@pytest.mark.asyncio
async def test_dismiss_popup_purchase_taps_close_never_cta(mocker, ) -> None:
    """PURCHASE must only ever tap the X, never the (primary) Buy CTA."""
    actions = _recording_actions()
    detector = _FakeDetector(
        [
            _state(PopupKind.PURCHASE, close=Point(480, 210), primary=Point(300, 700)),
            _state(PopupKind.NONE),
        ]
    )
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert actions._test_taps == [(480, 210, "popup_close")]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dismiss_popup_captcha_never_tapped(mocker, ) -> None:
    """A captcha is never dismissed — stop without tapping."""
    actions = _recording_actions()
    detector = _FakeDetector([_state(PopupKind.CAPTCHA, close=Point(480, 210))])
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert actions._test_taps == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dismiss_popup_escalates_with_no_safe_point(mocker, ) -> None:
    """Overlay present but no close/primary point → defer to the shotgun."""
    actions = _recording_actions()
    detector = _FakeDetector([_state(PopupKind.AD_WEBVIEW)])
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert actions._test_taps == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dismiss_popup_aborts_on_rejected_tap(mocker, ) -> None:
    """A rejected approval stops the loop instead of re-prompting every layer."""
    actions = _recording_actions()
    actions.tap.side_effect = None
    actions.tap.return_value = False  # operator rejects
    detector = _FakeDetector(
        [
            _state(PopupKind.SAFE_DISMISS, close=Point(480, 210)),
            _state(PopupKind.SAFE_DISMISS, close=Point(480, 210)),
        ]
    )
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx())

    assert detector.calls == 1
    assert actions.tap.call_count == 1


@pytest.mark.asyncio
async def test_dismiss_popup_respects_max_layers(mocker, ) -> None:
    """A modal that never clears is tapped at most ``max_layers`` times."""
    actions = _recording_actions()
    detector = _FakeDetector(
        [_state(PopupKind.SAFE_DISMISS, close=Point(480, 210)) for _ in range(10)]
    )
    _wire(mocker, actions, detector)

    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](_ctx({"max_layers": 2}))

    assert len(actions._test_taps) == 2  # type: ignore[attr-defined]
