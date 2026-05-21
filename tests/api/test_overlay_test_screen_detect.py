"""Overlay-test runs screen detection on the frame before overlay rules."""

from __future__ import annotations

from api.services.overlay_test import _detect_screen_on_frame


def test_detect_screen_on_frame_empty_image() -> None:
    detected, ms = _detect_screen_on_frame(None)
    assert detected == ""
    assert ms >= 0
