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

    def swipe(
        self, _inst: str, start: Any, end: Any, duration_ms: int = 0,
        min_duration_ms: int | None = None, settle_ms: int | None = None,
    ) -> bool:
        self.swipes.append((start, end, duration_ms, min_duration_ms, settle_ms))
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


async def _run(
    monkeypatch, *, screens: list[str], rows: list[dict[str, Any]]
) -> tuple[_FakeActions, DslExecContext]:
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
    ctx = _ctx()
    await fexec._exec_drive_fishing(ctx)
    return actions, ctx


def test_handler_is_registered() -> None:
    assert "drive_fishing" in fexec.DSL_EXEC_HANDLERS


def test_draw_decision_renders_without_crashing() -> None:
    # The debug annotated-frame renderer (only used with debug:true) must not
    # crash on a real-shaped frame + plan.
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    plan = {
        "hook_x": 360, "hook_y": 192, "phase": "collect", "level": 14,
        "hook_direction": "up",
        "swipe": {"from_x": 360, "from_y": 192, "to_x": 300, "to_y": 192},
    }
    out = fexec._draw_decision(frame, [_fish_row(420, 192)], plan)
    assert out is not None and out.shape == frame.shape


def test_swipe_motion_reports_actual_amplified_dx() -> None:
    # Small planned corrections are executed as stronger flicks; the hook
    # estimate must move by the real device gesture, not the raw plan dx.
    right = fexec._swipe_motion({"from_x": 360, "to_x": 400})
    assert right["raw_dx"] == 40
    assert right["executed_dx"] == fexec._SWIPE_MIN_PX

    left = fexec._swipe_motion({"from_x": 360, "to_x": 260})
    assert left["raw_dx"] == -100
    assert left["executed_dx"] == -int(abs(left["raw_dx"]) * fexec._SWIPE_GAIN)


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
    actions, ctx = await _run(
        monkeypatch,
        screens=["main_ready", "gameplay", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )

    # Started a round from the hub (live entry lands on the "Go Fish" state).
    assert any(label == "fishing_tournament.go_fish" for label, _pt in actions.taps)
    # Steered the hook at least once.
    assert actions.swipes, "expected at least one swipe on the gameplay screen"
    start, end, _dur, min_dur, settle = actions.swipes[0]
    assert end.x < start.x  # fish on the right → dodge flees left
    # Flick across the screen centre (zone-agnostic), not the top hook row.
    assert start.y == int(fexec._H * fexec._SWIPE_Y_FRAC)
    assert start.y == end.y  # horizontal
    # Fast flick: bypass the ~900 ms floor AND the 250 ms post-swipe settle.
    assert min_dur == fexec._SWIPE_MS
    assert settle == 0
    # Tuning telemetry is populated (the dodge/collect quality signal). The
    # exact tick count isn't pinned: while in gameplay the FSM assumes it's still
    # playing between full re-detects (the _SCREEN_RECHECK speed-up), so it
    # processes more gameplay ticks than the scripted "gameplay" frames.
    assert ctx.result["ticks"] >= 2
    assert ctx.result["phase_dodge"] + ctx.result["phase_collect"] == ctx.result["ticks"]
    assert ctx.result["swipe_left"] >= 1  # dodged left


async def test_stops_without_playing_when_already_home(monkeypatch) -> None:
    _AVAILABLE[0] = True
    actions, _ctx = await _run(monkeypatch, screens=["main_city"], rows=[_fish_row(420, 192)])
    assert actions.taps == []
    assert actions.swipes == []


async def test_pause_modal_is_resumed(monkeypatch) -> None:
    _AVAILABLE[0] = True
    actions, _ctx = await _run(
        monkeypatch,
        screens=["pause", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )
    assert any(label == "fishing_tournament.continue" for label, _pt in actions.taps)


async def test_haul_screen_exits_to_hub(monkeypatch) -> None:
    _AVAILABLE[0] = True
    # Landing on the post-round Haul summary → tap its "to fishing tournament"
    # button and stop (no swipe).
    actions, _ctx = await _run(
        monkeypatch,
        screens=["haul", "main_ready"],
        rows=[_fish_row(420, 192)],
    )
    assert any(
        label == "fishing_tournament_haul.to.fishing_tournament"
        for label, _pt in actions.taps
    )
    assert actions.swipes == []


async def test_no_inference_returns_early(monkeypatch) -> None:
    _AVAILABLE[0] = False
    actions, _ctx = await _run(
        monkeypatch,
        screens=["main_ready", "gameplay", "main_city"],
        rows=[_fish_row(420, 192)],
    )
    assert actions.swipes == []
    _AVAILABLE[0] = True  # reset for any later test
