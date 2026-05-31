from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np
import pytest
from conftest import make_actions

import tasks.dsl_exec as dsl_exec
from layout.types import Point, Region
from ocr.client import OCRResult
from popup.models import DetectionSignals, PopupKind, PopupState
from tasks import dsl_runtime

REPO_ROOT = Path(__file__).resolve().parents[2]
ADS_REFERENCES = REPO_ROOT / "games" / "wos" / "ads" / "references"


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
    screen_name: str | None = None,
) -> PopupState:
    return PopupState(
        kind=kind,
        bbox=Region(100, 200, 400, 600),
        close_point=close,
        primary_point=primary,
        card_text="",
        signals=_signals(overlay_present=kind != PopupKind.NONE),
        screen_name=screen_name,
    )


class _FakeDetector:
    """Yields a queued sequence of states, one per ``detect`` call."""

    def __init__(self, states: list[PopupState]) -> None:
        self._states = iter(states)
        self.calls = 0

    async def detect(self, _image: Any) -> PopupState:
        self.calls += 1
        return next(self._states, _state(PopupKind.NONE))


class _PriceOcr:
    async def ocr_region(
        self,
        _image: object,
        _bbox: object,
        *,
        region_id: str,
    ) -> OCRResult:
        return OCRResult(region_id=region_id, text="$4.99", confidence=1.0)


class _FakeRedis:
    def __init__(self) -> None:
        self.hsets: list[tuple[str, str, str]] = []

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hsets.append((key, field, value))
        return 1


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


def _wire(mocker, actions: Any, detector: _FakeDetector) -> None:
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(dsl_runtime, "ocr_client", return_value=object())
    mocker.patch.object(
        dsl_runtime,
        "settings",
        return_value=SimpleNamespace(worker=SimpleNamespace()),
    )
    mocker.patch.object(dsl_exec, "PopupDetector", lambda _ocr: detector)
    mocker.patch.object(dsl_exec, "_DISMISS_POPUP_SETTLE_S", 0)


def _wire_real_detector(mocker, actions: Any) -> None:
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(dsl_runtime, "ocr_client", return_value=_PriceOcr())
    mocker.patch.object(
        dsl_runtime,
        "settings",
        return_value=SimpleNamespace(worker=SimpleNamespace()),
    )
    mocker.patch.object(dsl_exec, "_DISMISS_POPUP_SETTLE_S", 0)


def _ctx(
    args: dict[str, Any] | None = None,
    *,
    redis_client: Any | None = None,
) -> dsl_exec.DslExecContext:
    return dsl_exec.DslExecContext(
        redis_client=redis_client,
        player_id="",
        instance_id="bs1",
        args=args or {},
    )


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

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](ctx)

    assert actions._test_taps == [(480, 210, "popup_close")]  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "no_popup"
    assert ctx.result["popup_action"] == "clear"
    assert ctx.result["popup_dismissed"] == 1


@pytest.mark.asyncio
async def test_dismiss_popup_known_page_defers_and_persists_screen(mocker, ) -> None:
    actions = _recording_actions()
    detector = _FakeDetector(
        [_state(PopupKind.PAGE, screen_name="welcome_back")]
    )
    redis = _FakeRedis()
    _wire(mocker, actions, detector)

    ctx = _ctx(redis_client=redis)
    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](ctx)

    assert actions._test_taps == []  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "screen_page"
    assert ctx.result["popup_action"] == "defer_to_screen"
    assert ctx.result["popup_screen"] == "welcome_back"
    assert redis.hsets == [
        ("wos:instance:bs1:state", "current_screen", "welcome_back")
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("screenshot", "close_x", "close_y"),
    [
        ("craftsmans_treasure.png", (640, 690), (205, 255)),
        ("ads_rookie_value_pack.png", (585, 635), (105, 155)),
        ("ads.legend_transcend_pack.png", (585, 635), (105, 155)),
    ],
)
async def test_dismiss_popup_exec_closes_real_purchase_ad_unknown_screen(
    mocker,
    screenshot: str,
    close_x: tuple[int, int],
    close_y: tuple[int, int],
) -> None:
    """The smart half of dismiss_unknown_popup closes real purchase ad popups."""
    frame = cv2.imread(str(ADS_REFERENCES / screenshot))
    assert frame is not None
    clear = np.zeros_like(frame)
    actions = make_actions(frames=[frame, clear])
    taps: list[tuple[int, int, Any]] = []

    def _tap(_instance_id: str, point: Any, **kwargs: object) -> bool:
        taps.append((point.x, point.y, kwargs.get("approval_region")))
        return True

    actions.tap.side_effect = _tap
    _wire_real_detector(mocker, actions)

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["dismiss_popup"](ctx)

    assert len(taps) == 1
    x, y, approval_region = taps[0]
    assert close_x[0] <= x <= close_x[1]
    assert close_y[0] <= y <= close_y[1]
    assert approval_region == "popup_close"
    assert ctx.result["reason"] == "no_popup"
    assert ctx.result["popup_action"] == "clear"
    assert ctx.result["popup_dismissed"] == 1
    assert ctx.result["popup_approval_region"] == "popup_close"
