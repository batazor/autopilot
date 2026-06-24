"""Tests for the decoupled fish-plan service (altitude ring-buffer + wiring)."""
from __future__ import annotations

import pytest

from api.services import fish_plan
from api.services.fish_common import FishDetectionRow
from api.services.fish_plan import _record_level, _take_prev_frame, reset_levels


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    fish_plan._LEVELS.clear()
    fish_plan._LAST_FRAME.clear()
    yield
    fish_plan._LEVELS.clear()
    fish_plan._LAST_FRAME.clear()


def _row(cx: int, cy: int) -> FishDetectionRow:
    return FishDetectionRow(
        x=cx, y=cy, width=10, height=10,
        center_x=cx, center_y=cy, confidence=0.9, class_name="fish",
    )


def test_record_level_accumulates() -> None:
    assert _record_level("bs1", 14, reset=False) == [14]
    assert _record_level("bs1", 15, reset=False) == [14, 15]
    assert _record_level("bs1", 16, reset=False) == [14, 15, 16]


def test_record_level_ignores_none() -> None:
    _record_level("bs1", 14, reset=False)
    assert _record_level("bs1", None, reset=False) == [14]  # OCR miss → unchanged


def test_record_level_reset_flag_clears_baseline() -> None:
    _record_level("bs1", 30, reset=False)
    assert _record_level("bs1", 5, reset=True) == [5]  # new round baseline


def test_record_level_resets_on_sharp_drop() -> None:
    _record_level("bs1", 40, reset=False)
    # a big drop (>_ROUND_RESET_DROP) means a fresh round started.
    assert _record_level("bs1", 2, reset=False) == [2]


def test_record_level_small_dip_is_kept() -> None:
    _record_level("bs1", 40, reset=False)
    # within the round-reset tolerance → treated as noise, history continues.
    assert _record_level("bs1", 38, reset=False) == [40, 38]


def test_record_level_caps_history() -> None:
    for i in range(fish_plan._MAX_LEVELS + 10):
        _record_level("bs1", i, reset=False)
    hist = fish_plan._LEVELS["bs1"]
    assert len(hist) == fish_plan._MAX_LEVELS
    assert hist[-1] == fish_plan._MAX_LEVELS + 9  # newest kept


def test_record_level_is_per_instance() -> None:
    _record_level("bs1", 10, reset=False)
    _record_level("bs2", 20, reset=False)
    assert fish_plan._LEVELS["bs1"] == [10]
    assert fish_plan._LEVELS["bs2"] == [20]


def test_reset_levels_drops_instance() -> None:
    _record_level("bs1", 14, reset=False)
    _take_prev_frame("bs1", [_row(10, 10)], 100.0, reset=False)
    reset_levels("bs1")
    assert "bs1" not in fish_plan._LEVELS
    assert "bs1" not in fish_plan._LAST_FRAME


# --- _take_prev_frame (velocity dt) ------------------------------------------
def test_take_prev_frame_first_call_has_no_prev() -> None:
    prev, dt = _take_prev_frame("bs1", [_row(10, 10)], 100.0, reset=False)
    assert prev is None
    assert dt is None


def test_take_prev_frame_returns_gap_and_prev_rows() -> None:
    _take_prev_frame("bs1", [_row(10, 10)], 100.0, reset=False)
    prev, dt = _take_prev_frame("bs1", [_row(20, 10)], 100.5, reset=False)
    assert dt == pytest.approx(0.5)
    assert prev is not None and prev[0]["center_x"] == 10  # the earlier frame


def test_take_prev_frame_stale_frame_yields_no_dt() -> None:
    _take_prev_frame("bs1", [_row(10, 10)], 100.0, reset=False)
    # same mtime (rolling preview hasn't advanced) → no usable velocity.
    _prev, dt = _take_prev_frame("bs1", [_row(20, 10)], 100.0, reset=False)
    assert dt is None


def test_take_prev_frame_reset_drops_prev() -> None:
    _take_prev_frame("bs1", [_row(10, 10)], 100.0, reset=False)
    prev, dt = _take_prev_frame("bs1", [_row(20, 10)], 100.5, reset=True)
    assert prev is None and dt is None
    assert fish_plan._LAST_FRAME["bs1"][1] == 100.5  # this frame is still stored


def test_fish_plan_route_served() -> None:
    # Routers are included in the lifespan startup, so drive it via TestClient.
    # An unknown instance proves the route is registered (404 with our detail,
    # not Starlette's bare "Not Found").
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as c:
        r = c.get("/api/instances/__nope__/fish-plan")
    assert r.status_code == 404
    assert "unknown instance" in r.json()["detail"]
