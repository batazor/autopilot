"""Overlay-test runs screen detection on the frame before overlay rules."""

from __future__ import annotations

import cv2
import numpy as np

from api.services import overlay_test
from api.services.overlay_test import _detect_screen_on_frame, cache
from api.services.overlay_test import run as overlay_test_run


def test_detect_screen_on_frame_empty_image() -> None:
    detected, ms = _detect_screen_on_frame(None)
    assert detected == ""
    assert ms >= 0


def test_run_screen_detect_skips_overlay_analysis(tmp_path, monkeypatch) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok
    with overlay_test._overlay_test_result_cache_lock:
        overlay_test._overlay_test_result_cache.clear()

    monkeypatch.setattr(cache, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cache,
        "load_preview_bytes",
        lambda **_k: (encoded.tobytes(), "temporal/bs1.png", 1.0),
    )
    monkeypatch.setattr(
        cache, "load_rolling_instance_preview", lambda _i: (None, "", None)
    )
    monkeypatch.setattr(
        "dashboard.redis_client.get_instance_state",
        lambda *_a, **_k: {"current_screen": "main_city"},
    )
    monkeypatch.setattr(
        cache,
        "_detect_screen_on_frame",
        lambda _img, **_k: ("dreamscape_memory", 7),
    )

    def _no_overlay(*_args, **_kwargs):
        msg = "screen-detect must not run overlay analysis"
        raise AssertionError(msg)

    monkeypatch.setattr(overlay_test_run, "run_overlay_analysis_sync", _no_overlay)

    result = overlay_test.run_screen_detect(client=object(), instance_id="bs1")

    assert result["detected_screen"] == "dreamscape_memory"
    assert result["screen_source"] == "detected"
    assert result["duration_ms"] == 7
    assert result["preview"]["available"] is True
    assert result["preview"]["width"] == 720
