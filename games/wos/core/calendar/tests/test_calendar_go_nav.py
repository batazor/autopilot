"""Tests for Go-button navigation (name matcher + the navigate loop)."""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import pytest
from games.wos.core.calendar import go_nav

_SAMPLES = Path(__file__).resolve().parent.parent / "references" / "samples"


def test_name_matches_exact_substring_fuzzy():
    assert go_nav.name_matches("Alliance Championship", ["Alliance Championship"])
    assert go_nav.name_matches("Alliance Championship", ["Alliance"])      # substring
    assert go_nav.name_matches("Allianee Championshlp", ["Alliance Championship"])  # OCR noise
    assert not go_nav.name_matches("Hero Rally", ["Alliance Championship"])
    assert not go_nav.name_matches("", ["Alliance"])


class _FakeActions:
    """Replays a scroll where tapping a bar reveals one popup fixture."""

    def __init__(self, calendar, popup) -> None:
        self._cal = calendar
        self._popup = popup
        self.state = "cal"
        self.go_tapped = False
        self.regions: list[str] = []

    def capture_screen_bgr(self, instance_id):
        return self._popup if self.state == "popup" else self._cal

    def tap(self, instance_id, point, **kwargs):
        region = kwargs.get("approval_region")
        self.regions.append(region)
        if region == "calendar.event_bar":
            self.state = "popup"
        elif region == "calendar.dismiss":
            self.state = "cal"
        elif region == "calendar.go":
            self.go_tapped = True
        return True

    def swipe(self, instance_id, start, end, duration_ms):
        return True


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
async def test_navigate_via_go_taps_go_on_match():
    cal = cv2.imread(str(_SAMPLES / "calendar_0_top.png"))
    popup = cv2.imread(str(_SAMPLES / "event_detail_popup_2.png"))  # Alliance Championship
    if cal is None or popup is None:
        pytest.skip("fixtures missing")
    from config.loader import load_settings
    from ocr.client import OcrClient

    ocr = OcrClient(load_settings())._run_tesseract
    actions = _FakeActions(cal, popup)
    found = await go_nav.navigate_via_go(
        actions, "inst", ocr, ["Alliance Championship"], max_swipes=0
    )
    assert found is True
    assert actions.go_tapped is True
    assert "calendar.go" in actions.regions


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
async def test_navigate_via_go_returns_false_when_no_match():
    cal = cv2.imread(str(_SAMPLES / "calendar_0_top.png"))
    popup = cv2.imread(str(_SAMPLES / "event_detail_popup_2.png"))
    if cal is None or popup is None:
        pytest.skip("fixtures missing")
    from config.loader import load_settings
    from ocr.client import OcrClient

    ocr = OcrClient(load_settings())._run_tesseract
    actions = _FakeActions(cal, popup)
    found = await go_nav.navigate_via_go(
        actions, "inst", ocr, ["Totally Different Event"], max_swipes=0
    )
    assert found is False
    assert actions.go_tapped is False
