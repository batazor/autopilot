"""Regression: bbox patch equals exported crop at identical resolution (1:1)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from layout.template_match import (
    match_crop_1to1_at_bbox_percent,
    match_template_full_frame_cached,
    validate_live_bbox_patch_vs_reference_dims,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_SKIP_FULL = REPO_ROOT / "references" / "skip_button.png"
_SKIP_CROP = REPO_ROOT / "references" / "crop" / "skip_button_skip_button.png"
_AREA_JSON = REPO_ROOT / "area.json"


@pytest.mark.skipif(
    not _SKIP_FULL.is_file() or not _SKIP_CROP.is_file() or not _AREA_JSON.is_file(),
    reason="skip_button reference assets or area.json missing",
)
def test_skip_button_crop_1to1_matches_bbox_patch() -> None:
    doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    screens = doc.get("screens") or []
    screen = next(
        (s for s in screens if Path(str(s.get("ocr") or "")).stem == _SKIP_FULL.stem),
        None,
    )
    assert screen is not None, "area.json must contain an entry for references/skip_button.png"

    region = next(
        (r for r in screen.get("regions") or [] if str(r.get("name")) == "skip_button"),
        None,
    )
    assert region is not None and region.get("bbox"), "skip_button region with bbox expected"

    bbox = region["bbox"]
    full_bgr = cv2.imread(str(_SKIP_FULL))
    crop_bgr = cv2.imread(str(_SKIP_CROP))
    assert full_bgr is not None and crop_bgr is not None, "OpenCV must load PNG assets"

    hi, wi = full_bgr.shape[:2]
    exp_x = int(bbox["x"] / 100.0 * wi)
    exp_y = int(bbox["y"] / 100.0 * hi)

    result = match_crop_1to1_at_bbox_percent(full_bgr, crop_bgr, bbox)

    assert result["score"] >= 0.99
    assert result["top_left"] == (exp_x, exp_y)


def test_validate_live_vs_reference_small_requires_exact_dims() -> None:
    with pytest.raises(ValueError, match="exactly \\(1:1\\)"):
        validate_live_bbox_patch_vs_reference_dims(
            10, 8, 157, 74, reference_label="exported crop"
        )


def test_validate_live_vs_reference_large_within_tolerance_ok() -> None:
    validate_live_bbox_patch_vs_reference_dims(
        100, 50, 105, 52, reference_label="exported crop"
    )


def test_full_frame_cached_match_uses_cached_position(monkeypatch: pytest.MonkeyPatch) -> None:
    image = cv2.imread(str(_SKIP_FULL))
    template = cv2.imread(str(_SKIP_CROP))
    if image is None or template is None:
        pytest.skip("skip_button reference assets missing")

    cached: list[tuple[int, int, float]] = []
    monkeypatch.setattr(
        "layout.template_match.read_positions",
        lambda key: [{"x": 6, "y": 13, "score": 0.99, "last_seen": 1.0, "hits": 3}],
    )
    monkeypatch.setattr(
        "layout.template_match.record_position",
        lambda key, *, x, y, score: cached.append((x, y, score)),
    )

    # Use an exact cached top-left from the fixture bbox.
    doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    screen = next(s for s in doc.get("screens") or [] if Path(str(s.get("ocr") or "")).stem == _SKIP_FULL.stem)
    region = next(r for r in screen.get("regions") or [] if str(r.get("name")) == "skip_button")
    hi, wi = image.shape[:2]
    x = int(float(region["bbox"]["x"]) / 100.0 * wi)
    y = int(float(region["bbox"]["y"]) / 100.0 * hi)
    monkeypatch.setattr(
        "layout.template_match.read_positions",
        lambda key: [{"x": x, "y": y, "score": 0.99, "last_seen": 1.0, "hits": 3}],
    )

    row = match_template_full_frame_cached(
        image,
        template,
        cache_key="test-cache-hit",
        threshold=0.99,
    )

    assert row.get("matched", True)
    assert row["top_left"] == (x, y)
    assert row["match_source"] == "cache"
    assert cached and cached[0][0:2] == (x, y)


def test_full_frame_cached_match_falls_back_to_full_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    image = cv2.imread(str(_SKIP_FULL))
    template = cv2.imread(str(_SKIP_CROP))
    if image is None or template is None:
        pytest.skip("skip_button reference assets missing")

    recorded: list[tuple[int, int, float]] = []
    monkeypatch.setattr("layout.template_match.read_positions", lambda key: [])
    monkeypatch.setattr(
        "layout.template_match.record_position",
        lambda key, *, x, y, score: recorded.append((x, y, score)),
    )

    row = match_template_full_frame_cached(
        image,
        template,
        cache_key="test-full-frame",
        threshold=0.99,
    )

    assert row["score"] >= 0.99
    assert row["match_source"] == "full_frame_hash"
    assert recorded
