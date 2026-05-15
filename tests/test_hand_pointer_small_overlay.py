"""Tests for ``hand_pointer_small.visible``.

The diagnostic “live” frame checked into the repo is::

    tests/fixtures/hand_pointer_small_screen.png

Copy this from the rolling ADB preview when the tutorial shows the small hand pointer (dialogue,
yellow CTA, etc.). A corner triangle / “next” cue can also satisfy ``skip_text_button.visible`` on
the **same** PNG.

On that single file we assert **both** overlays match. Queue ordering still prefers the pointer
scenario (86_000 vs skip 85_000); see ``scheduler/queue.pop_due``.

Separate tests cover ``references/hand_pointer_small.png`` (labeling reference) and half‑resolution mismatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from analysis.overlay import evaluate_overlay_rules
from analysis.overlay_manifest import load_merged_analyze_yaml
from ocr.client import OCRResult

REPO = Path(__file__).resolve().parents[1]

_REF_PNG = REPO / "references" / "hand_pointer_small.png"
_CROP_PNG = REPO / "references" / "crop" / "hand_pointer_small_hand_pointer_small.png"
_AREA = REPO / "area.json"
# Repo-relative path; see module docstring.
_FIXTURE_ADB_SCREEN = REPO / "tests" / "fixtures" / "hand_pointer_small_screen.png"
_FIXTURE_REL = "tests/fixtures/hand_pointer_small_screen.png"

_RULE_HP = "hand_pointer_small.visible"
_RULE_SKIP = "skip_text_button.visible"

_DEPS_CORE = _REF_PNG.is_file() and _CROP_PNG.is_file() and _AREA.is_file()
_DEPS_FIXTURE = _DEPS_CORE and _FIXTURE_ADB_SCREEN.is_file()


class _StubOcrClient:
    async def ocr_regions(
        self,
        _image_bgr,
        regions,
        *,
        region_ids: list[str] | None = None,
        region_preprocess: list[str | None] | None = None,
    ) -> list[OCRResult]:
        ids = region_ids or [f"r{i}" for i in range(len(regions))]
        return [OCRResult(region_id=rid, text="", confidence=0.0) for rid in ids]


@pytest.fixture(autouse=True)
def _stub_overlay_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.get_ocr_client", lambda: _StubOcrClient())


@pytest.fixture(scope="module")
def hand_pointer_small_adb_screen_bgr():
    """BGR image loaded from ``tests/fixtures/hand_pointer_small_screen.png`` (frozen ADB frame)."""
    img = cv2.imread(str(_FIXTURE_ADB_SCREEN))
    assert img is not None, f"missing or unreadable {_FIXTURE_REL}"
    return img


def _merged_overlay_and_area() -> tuple[list, dict]:
    doc = json.loads(_AREA.read_text(encoding="utf-8"))
    cfg = load_merged_analyze_yaml(REPO)
    overlay = cfg.get("overlay")
    assert isinstance(overlay, list)
    return overlay, doc


def _overlay_out(img) -> dict:
    overlay, doc = _merged_overlay_and_area()
    return evaluate_overlay_rules(img, doc, REPO, overlay)


def _rule(out: dict, logical: str) -> dict:
    r = out.get(logical)
    return r if isinstance(r, dict) else {}


def _digest_row(out: dict, logical: str) -> dict[str, object]:
    r = _rule(out, logical)
    return {
        "matched": r.get("matched"),
        "score": r.get("score"),
        "threshold": r.get("threshold"),
        "reason": r.get("reason"),
    }


@pytest.mark.skipif(
    not _DEPS_FIXTURE,
    reason=f"crop/analyze/area or {_FIXTURE_REL} missing",
)
def test_hand_pointer_small_screen_fixture_png_overlay_hand_vs_skip(
    hand_pointer_small_adb_screen_bgr,
) -> None:
    """On ``tests/fixtures/hand_pointer_small_screen.png``, pointer and skip both match → two ``pushScenario`` hooks (pointer wins by priority)."""
    out = _overlay_out(hand_pointer_small_adb_screen_bgr)
    hp = _digest_row(out, _RULE_HP)
    sk = _digest_row(out, _RULE_SKIP)

    assert hp["matched"] is True, hp
    assert float(hp["score"] or 0) >= float(hp["threshold"] or 0)

    assert sk["matched"] is True, (
        "refresh the fixture from a frame where both the pointer and Skip/next are visible; "
        f"got {sk}"
    )
    assert float(sk["score"] or 0) >= float(sk["threshold"] or 0)


@pytest.mark.skipif(not _DEPS_CORE, reason="hand_pointer_small reference/crop/analyze/area missing")
def test_hand_pointer_small_visible_matches_on_labeled_reference_png() -> None:
    """Labeling reference ``references/hand_pointer_small.png`` must match the small-pointer overlay."""
    img = cv2.imread(str(_REF_PNG))
    assert img is not None
    row = _rule(_overlay_out(img), _RULE_HP)
    assert row.get("matched") is True, row
    assert float(row.get("score") or 0) >= float(row.get("threshold") or 0)


@pytest.mark.skipif(not _DEPS_CORE, reason="hand_pointer_small assets missing")
def test_hand_pointer_small_visible_fails_when_frame_scale_differs_from_reference() -> None:
    """Half‑resolution framebuffer breaks fixed-pixel template match."""
    img = cv2.imread(str(_REF_PNG))
    assert img is not None
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    row = _rule(_overlay_out(small), _RULE_HP)
    assert row.get("matched") is False
    thr = float(row.get("threshold") or 1.0)
    assert float(row.get("score") or 0) < thr
