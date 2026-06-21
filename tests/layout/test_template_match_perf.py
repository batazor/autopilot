"""Perf-oriented template match behavior (early exit, lazy hybrid)."""

from __future__ import annotations

import cv2
import numpy as np

from layout import template_match as tm


def test_ncc_peaks_early_exit_on_threshold() -> None:
    rng = np.random.default_rng(0)
    icon = rng.integers(0, 256, size=(24, 24, 3), dtype=np.uint8)
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[40:64, 50:74] = icon

    heat = np.full((177, 277), -1.0, dtype=np.float32)
    heat[40, 50] = 0.95

    calls = {"hybrid": 0}
    real = tm._hybrid_scores_at_patch

    def spy(*args: object, **kwargs: object) -> object:
        calls["hybrid"] += 1
        return real(*args, **kwargs)

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tm, "_hybrid_scores_at_patch", spy)
    try:
        out = tm._best_phash_among_ncc_peaks(
            frame,
            icon,
            (0, 0),
            heat,
            max_peaks=10,
            threshold=0.8,
        )
    finally:
        monkeypatch.undo()

    assert out is not None
    assert float(out["score"]) >= 0.8
    assert calls["hybrid"] == 1


def test_shared_gray_reuses_frame_conversion() -> None:
    rng = np.random.default_rng(1)
    tpl = rng.integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    frame[30:46, 40:56] = tpl
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bbox = {
        "x": 20.0,
        "y": 20.0,
        "width": 50.0,
        "height": 50.0,
        "original_width": 160.0,
        "original_height": 120.0,
    }
    a = tm.match_template_in_search_roi_bbox_percent(frame, tpl, bbox, image_gray=gray)
    b = tm.match_template_in_search_roi_bbox_percent(frame, tpl, bbox)
    assert abs(float(a["score"]) - float(b["score"])) < 0.05
