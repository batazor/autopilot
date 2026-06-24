"""Fishing Tournament minigame driver (``exec: drive_fishing``).

The pure decision (phase + swipe) is covered by the ``fish_engine`` tests; these
exercise the on-device FSM *wiring*: that it enters a round from the hub, turns a
``plan_action`` swipe into an ADB swipe while on the gameplay screen, and stops
cleanly when it leaves the event. The device, detector, OCR and screen detector
are all faked — like the on-device-validated ``tundra_trek`` FSM, the live timing
is tuned on a real run, not unit-tested.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
from games.wos.events.fishing_tournament import exec as fexec

from tasks.dsl_exec.context import DslExecContext


class _FakeActions:
    def __init__(self, frame: Any) -> None:
        self.frame = frame
        self.taps: list[tuple[str | None, Any]] = []
        self.swipes: list[tuple[Any, Any, int]] = []
        self.backs = 0

    def capture_screen_bgr(self, _inst: str) -> Any:
        return self.frame

    def tap(self, _inst: str, pt: Any, approval_region: str | None = None) -> bool:
        self.taps.append((approval_region, pt))
        return True

    def swipe(self, _inst: str, start: Any, end: Any, duration_ms: int = 0) -> bool:
        self.swipes.append((start, end, duration_ms))
        return True

    def system_back(self, _inst: str) -> bool:
        self.backs += 1
        return True


class _FakeOcr:
    def _run_tesseract(self, _crop: Any, preprocess: str | None = None) -> tuple[str, float]:
        return ("", 0.0)


class _FakeScreens:
    """Yields a scripted screen sequence; repeats the last entry when exhausted."""

    def __init__(self, seq: list[str]) -> None:
        self.seq = seq
        self.i = 0

    async def detect_screen(self, _frame: Any, **_kw: Any) -> str:
        s = self.seq[self.i] if self.i < len(self.seq) else self.seq[-1]
        self.i += 1
        return s


class _FakeFish:
    model_id = "fake/1"

    def __init__(self, available: bool = True) -> None:
        self._available = available

    @classmethod
    def from_settings(cls, _cfg: Any) -> _FakeFish:
        return cls(available=_AVAILABLE[0])

    def available(self) -> bool:
        return self._available

    async def detect(self, _frame: Any, threshold: float | None = None) -> list[Any]:
        return []  # rows come from the patched detections_to_rows


# Toggled per test so _FakeFish.from_settings reflects "inference configured?".
_AVAILABLE = [True]


def _fish_row(cx: int, cy: int) -> dict[str, Any]:
    return {
        "x": cx - 20, "y": cy - 12, "width": 40, "height": 24,
        "center_x": cx, "center_y": cy, "class_name": "fish", "confidence": 0.9,
    }


def _ctx() -> DslExecContext:
    return DslExecContext(
        redis_client=None, player_id="", instance_id="bs1", args={"threshold": 0.4}
    )


async def _run(monkeypatch, *, screens: list[str], rows: list[dict[str, Any]]) -> _FakeActions:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    actions = _FakeActions(frame)
    monkeypatch.setattr("tasks.dsl_runtime.bot_actions", lambda: actions)
    monkeypatch.setattr("tasks.dsl_runtime.ocr_client", lambda: _FakeOcr())
    monkeypatch.setattr("navigation.detector.ScreenDetector", lambda _ocr=None: _FakeScreens(list(screens)))
    monkeypatch.setattr("inference.roboflow_client.RoboflowDetector", _FakeFish)
    monkeypatch.setattr("api.services.fish_common.detections_to_rows", lambda _dets: rows)
    # The synthetic frame has no real button pixels; entry detection is covered
    # separately on real frames. Here, pretend the hub shows the Go Fish CTA.
    monkeypatch.setattr(
        fexec, "_find_entry_button",
        lambda _frame: ("fishing_tournament.go_fish", (0.5, 0.848)),
    )
    # Don't actually sleep between ticks (a plain no-op coroutine — must not
    # call the patched asyncio.sleep, or it recurses).
    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)
    await fexec._exec_drive_fishing(_ctx())
    return actions


def test_handler_is_registered() -> None:
    assert "drive_fishing" in fexec.DSL_EXEC_HANDLERS


def test_find_entry_button_handles_both_hub_states() -> None:
    # Resume state → Go Fish; choose-mode → the free Ice Fishing button; the
    # gameplay screen has neither. Locates whichever the hub is showing so the
    # FSM can start a round regardless of state (the paid Frosty is never tapped).
    import cv2

    refs = Path(__file__).resolve().parents[1] / "references"
    resume = fexec._find_entry_button(cv2.imread(str(refs / "main_ready_go_fish.png")))
    choose = fexec._find_entry_button(cv2.imread(str(refs / "main_ready.png")))
    gameplay = fexec._find_entry_button(cv2.imread(str(refs / "gameplay.png")))

    assert resume is not None and resume[0] == "fishing_tournament.go_fish"
    assert choose is not None and choose[0] == "fishing_tournament.play.free"
    assert gameplay is None


async def test_enters_round_and_swipes_on_gameplay(monkeypatch) -> None:
    _AVAILABLE[0] = True
    # hub → tap play; then two gameplay frames with a fish to the right of the
    # fallback hook (360,192) → a leftward dodge swipe; then leave to the city.
    actions = await _run(
        monkeypatch,
        screens=["main_ready", "gameplay", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )

    # Started a round from the hub (live entry lands on the "Go Fish" state).
    assert any(label == "fishing_tournament.go_fish" for label, _pt in actions.taps)
    # Steered the hook at least once.
    assert actions.swipes, "expected at least one swipe on the gameplay screen"
    start, end, _dur = actions.swipes[0]
    assert end.x < start.x  # fish on the right → dodge flees left
    assert start.y == 192   # swipe is horizontal at the (fallback) hook row


async def test_stops_without_playing_when_already_home(monkeypatch) -> None:
    _AVAILABLE[0] = True
    actions = await _run(monkeypatch, screens=["main_city"], rows=[_fish_row(420, 192)])
    assert actions.taps == []
    assert actions.swipes == []


async def test_pause_modal_is_resumed(monkeypatch) -> None:
    _AVAILABLE[0] = True
    actions = await _run(
        monkeypatch,
        screens=["pause", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )
    assert any(label == "fishing_tournament.continue" for label, _pt in actions.taps)


async def test_no_inference_returns_early(monkeypatch) -> None:
    _AVAILABLE[0] = False
    actions = await _run(
        monkeypatch,
        screens=["main_ready", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )
    assert actions.swipes == []
    _AVAILABLE[0] = True  # reset for any later test
