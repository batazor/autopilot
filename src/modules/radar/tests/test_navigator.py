"""Pure-logic tests for the building navigator's step planner (no device)."""

import json
import math

import pytest

from modules.radar.navigator import _MAX_FINGER, latest_city_run, plan_step


def test_drag_is_opposite_the_camera_move():
    # Target to the RIGHT (and below) of current → drag finger left/up to bring
    # the camera there (content moves opposite the finger).
    fx, fy = plan_step((100, 100), (300, 250), scale=(1.0, 1.0))
    assert fx < 0 and fy < 0
    assert math.isclose(fx, -200) and math.isclose(fy, -150)


def test_scale_converts_canvas_to_finger():
    # 2 camera px per finger px → finger travel is half the canvas delta.
    fx, _ = plan_step((0, 0), (200, 0), scale=(2.0, 1.0))
    assert math.isclose(fx, -100)


def test_step_is_clamped_on_screen():
    fx, fy = plan_step((0, 0), (5000, 0), scale=(1.0, 1.0))
    assert math.isclose(math.hypot(fx, fy), _MAX_FINGER)


def test_zero_scale_is_safe():
    fx, fy = plan_step((0, 0), (100, 100), scale=(0.0, 0.0))
    assert math.isfinite(fx) and math.isfinite(fy)


@pytest.mark.parametrize("target", [(0, 0), (50, 0), (0, 50)])
def test_already_there_is_small_step(target):
    fx, fy = plan_step((0, 0), target, scale=(1.0, 1.0))
    assert math.hypot(fx, fy) <= 50


def _mk_run(d, target="main_city", with_map=True):
    d.mkdir(parents=True, exist_ok=True)
    if with_map:
        (d / "map_full.png").write_bytes(b"x")
    (d / "buildings.json").write_text("{}")
    (d / "manifest.json").write_text(json.dumps({"config": {"target": target}}))


def test_latest_city_run_picks_newest_main_city(tmp_path):
    _mk_run(tmp_path / "old")
    _mk_run(tmp_path / "new")
    _mk_run(tmp_path / "world", target="global_map")  # wrong target → ignored
    _mk_run(tmp_path / "noimg", with_map=False)        # no canvas → ignored
    # make "new" the newest
    (tmp_path / "new" / "manifest.json").touch()
    got = latest_city_run(tmp_path)
    assert got is not None and got.name in {"old", "new"}
    assert got == max(
        [tmp_path / "old", tmp_path / "new"], key=lambda p: p.stat().st_mtime
    )


def test_latest_city_run_none_when_no_scan(tmp_path):
    assert latest_city_run(tmp_path) is None
    assert latest_city_run(tmp_path / "missing") is None


def test_navigate_handler_registered():
    from games.wos.core.building.common.exec import DSL_EXEC_HANDLERS

    assert "navigate_to_building" in DSL_EXEC_HANDLERS


# --- robustness ---------------------------------------------------------------

import numpy as np  # noqa: E402

from modules.radar.navigator import Navigator, open_tap_point, route_decision  # noqa: E402


def test_route_decision():
    assert route_decision(50, 0, tol=90, patience=3) == "done"
    assert route_decision(200, 3, tol=90, patience=3) == "stalled"
    assert route_decision(200, 1, tol=90, patience=3) == "go"


def test_open_tap_is_below_centre():
    x, y = open_tap_point()
    assert x == 360.0 and y > 640.0


def _nav(target):
    return Navigator(
        canvas=np.zeros((10, 10, 3), np.uint8),
        buildings={"furnace": (target, "Furnace")},
        scale=(1.0, 1.0),
        crop={"x": 0, "y": 0, "w": 720, "h": 1280},
    )


def test_route_to_converges(monkeypatch):
    nav = _nav((500.0, 480.0))
    pos = [100.0, 100.0]
    monkeypatch.setattr(nav, "locate", lambda _f: (pos[0], pos[1]))

    def swipe(x1, y1, x2, y2):
        pos[0] += -(x2 - x1)  # camera moves opposite the finger (scale 1)
        pos[1] += -(y2 - y1)

    ok = nav.route_to("furnace", lambda: None, swipe, settle_s=0.0, max_steps=20)
    assert ok
    assert math.hypot(pos[0] - 500, pos[1] - 480) <= 90


def test_route_to_gives_up_when_never_localized(monkeypatch):
    nav = _nav((500.0, 480.0))
    monkeypatch.setattr(nav, "locate", lambda _f: None)
    swipes = []
    ok = nav.route_to(
        "furnace", lambda: None, lambda *a: swipes.append(a), settle_s=0.0, max_steps=5
    )
    assert ok is False


def test_on_lost_is_invoked_before_giving_up(monkeypatch):
    nav = _nav((500.0, 480.0))
    monkeypatch.setattr(nav, "locate", lambda _f: None)  # never localizes
    called = []
    ok = nav.route_to(
        "furnace", lambda: None, lambda *_a: None,
        settle_s=0.0, max_steps=3, on_lost=lambda: called.append(1),
    )
    assert ok is False
    assert called  # the popup-dismiss hook ran at least once


def test_route_to_stops_when_stalled(monkeypatch):
    nav = _nav((5000.0, 0.0))  # far, and we pin the position so it never moves
    monkeypatch.setattr(nav, "locate", lambda _f: (0.0, 0.0))
    calls = []
    nav.route_to(
        "furnace", lambda: None, lambda *a: calls.append(a),
        settle_s=0.0, max_steps=50, patience=3,
    )
    # Stalled out well before max_steps (no progress) rather than swiping 50×.
    assert len(calls) <= 6
