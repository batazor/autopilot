"""Template PNG cache + ``prefer_primary_bbox`` early-exit for findIcon.

Covers two perf paths introduced in ``analysis/overlay_engine.py``:

* ``_load_template_cached`` caches decoded PNG by ``(path, mtime_ns)`` so the
  same crop isn't re-decoded on every overlay tick.
* ``prefer_primary_bbox: true`` on a findIcon rule short-circuits the sliding
  search inside ``search_region`` when the cheap 1:1 match at the primary
  bbox already passes threshold + gates.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from analysis import overlay_engine
from analysis.overlay_engine import _load_template_cached, evaluate_overlay_rules_async


def _write_png(path: Path, fill: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((8, 8, 3), fill, dtype=np.uint8)
    cv2.imwrite(str(path), img)


def test_template_cache_returns_same_array_on_repeat(tmp_path: Path) -> None:
    overlay_engine._template_cache.clear()
    p = tmp_path / "t.png"
    _write_png(p, 200)
    a = _load_template_cached(p)
    b = _load_template_cached(p)
    assert a is not None and b is not None
    assert a is b


def test_template_cache_invalidates_on_mtime_change(tmp_path: Path) -> None:
    overlay_engine._template_cache.clear()
    p = tmp_path / "t.png"
    _write_png(p, 100)
    a = _load_template_cached(p)
    assert a is not None
    _write_png(p, 250)
    # Coarse filesystems can preserve the original mtime when writes happen
    # within the same clock tick; force a bump so the cache key changes.
    bumped_ns = p.stat().st_mtime_ns + 10_000_000
    os.utime(str(p), ns=(p.stat().st_atime_ns, bumped_ns))
    b = _load_template_cached(p)
    assert b is not None
    assert a is not b
    assert int(b[0, 0, 0]) == 250


def test_template_cache_returns_none_on_missing_file(tmp_path: Path) -> None:
    overlay_engine._template_cache.clear()
    assert _load_template_cached(tmp_path / "missing.png") is None


def _make_repo_with_icon(
    tmp_path: Path,
    *,
    place_icon_inside_primary: bool,
) -> tuple[Path, np.ndarray, dict[str, Any], dict[str, float], dict[str, float]]:
    """Build a fake repo layout + frame for findIcon evaluation.

    ``place_icon_inside_primary=True`` puts the 32×32 icon exactly at the primary
    bbox (cheap 1:1 match will pass). ``False`` shifts it inside ``search_region``
    so only the sliding search can find it.
    """
    repo_root = tmp_path
    refs = repo_root / "references"
    (refs / "crop").mkdir(parents=True)

    # Reference frame: 1280×720 with a distinctive 32×32 icon at (100, 100).
    # Random-but-deterministic pixels make the NCC peak unique inside the
    # search ROI (a flat color block produces a plateau of equally-good
    # matches and the picker drifts off the primary bbox).
    rng = np.random.default_rng(seed=42)
    icon = rng.integers(0, 256, size=(32, 32, 3), dtype=np.uint8)
    ref = np.zeros((720, 1280, 3), dtype=np.uint8)
    ref[100:132, 100:132] = icon
    cv2.imwrite(str(refs / "fr.png"), ref)

    # Exported crop = the icon patch itself.
    crop = ref[100:132, 100:132].copy()
    cv2.imwrite(str(refs / "crop" / "fr_btn.png"), crop)

    # Live frame: same icon, optionally shifted to (160, 100) — still inside
    # the search bbox below, but no longer at the primary bbox.
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if place_icon_inside_primary:
        frame[100:132, 100:132] = icon
    else:
        frame[100:132, 160:192] = icon

    bbox = {
        "x": 100.0 / 1280.0 * 100.0,
        "y": 100.0 / 720.0 * 100.0,
        "width": 32.0 / 1280.0 * 100.0,
        "height": 32.0 / 720.0 * 100.0,
    }
    search_bbox = {
        "x": 80.0 / 1280.0 * 100.0,
        "y": 80.0 / 720.0 * 100.0,
        "width": 200.0 / 1280.0 * 100.0,
        "height": 100.0 / 720.0 * 100.0,
    }
    area_doc: dict[str, Any] = {
        "screens": [
            {
                "ocr": "references/fr.png",
                "regions": [
                    {"name": "btn", "bbox": bbox},
                    {"name": "btn_search", "bbox": search_bbox},
                ],
            }
        ]
    }
    return repo_root, frame, area_doc, bbox, search_bbox


@pytest.mark.asyncio
async def test_prefer_primary_bbox_skips_sliding_when_primary_matches(
    tmp_path: Path, monkeypatch: Any
) -> None:
    overlay_engine._template_cache.clear()
    repo_root, frame, area_doc, _bbox, _sbb = _make_repo_with_icon(
        tmp_path, place_icon_inside_primary=True
    )

    calls = {"sliding": 0}
    real_sliding = overlay_engine.match_template_in_search_roi_bbox_percent

    def spy_sliding(*args: Any, **kwargs: Any) -> Any:
        calls["sliding"] += 1
        return real_sliding(*args, **kwargs)

    monkeypatch.setattr(
        overlay_engine, "match_template_in_search_roi_bbox_percent", spy_sliding
    )

    rule = {
        "name": "test.btn",
        "action": "findIcon",
        "region": "btn",
        "threshold": 0.8,
        "prefer_primary_bbox": True,
    }
    out = await evaluate_overlay_rules_async(frame, area_doc, repo_root, [rule])

    hit = out.get("test.btn")
    assert isinstance(hit, dict), out
    assert hit.get("matched") is True, hit
    assert calls["sliding"] == 0, "early-exit should have consumed the match"


@pytest.mark.asyncio
async def test_without_flag_sliding_search_still_runs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    overlay_engine._template_cache.clear()
    repo_root, frame, area_doc, _bbox, _sbb = _make_repo_with_icon(
        tmp_path, place_icon_inside_primary=True
    )

    calls = {"sliding": 0}
    real_sliding = overlay_engine.match_template_in_search_roi_bbox_percent

    def spy_sliding(*args: Any, **kwargs: Any) -> Any:
        calls["sliding"] += 1
        return real_sliding(*args, **kwargs)

    monkeypatch.setattr(
        overlay_engine, "match_template_in_search_roi_bbox_percent", spy_sliding
    )

    rule = {
        "name": "test.btn",
        "action": "findIcon",
        "region": "btn",
        "threshold": 0.8,
        # no prefer_primary_bbox
    }
    out = await evaluate_overlay_rules_async(frame, area_doc, repo_root, [rule])

    hit = out.get("test.btn")
    assert isinstance(hit, dict) and hit.get("matched") is True
    assert calls["sliding"] == 1


@pytest.mark.asyncio
async def test_prefer_primary_bbox_falls_through_when_primary_misses(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Icon is inside search_region but not at primary bbox — sliding must run."""
    overlay_engine._template_cache.clear()
    repo_root, frame, area_doc, _bbox, _sbb = _make_repo_with_icon(
        tmp_path, place_icon_inside_primary=False
    )

    calls = {"sliding": 0}
    real_sliding = overlay_engine.match_template_in_search_roi_bbox_percent

    def spy_sliding(*args: Any, **kwargs: Any) -> Any:
        calls["sliding"] += 1
        return real_sliding(*args, **kwargs)

    monkeypatch.setattr(
        overlay_engine, "match_template_in_search_roi_bbox_percent", spy_sliding
    )

    rule = {
        "name": "test.btn",
        "action": "findIcon",
        "region": "btn",
        "threshold": 0.8,
        "prefer_primary_bbox": True,
    }
    out = await evaluate_overlay_rules_async(frame, area_doc, repo_root, [rule])

    hit = out.get("test.btn")
    assert isinstance(hit, dict) and hit.get("matched") is True, hit
    assert calls["sliding"] == 1, "primary bbox missed, sliding should have run"
