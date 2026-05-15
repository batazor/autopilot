"""Overlay rule ``skip_button.visible`` matches labeled reference frame."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from analysis.overlay import evaluate_overlay_rules
from analysis.overlay_manifest import load_merged_analyze_yaml
from ocr.client import OCRResult


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

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not (REPO / "references/skip_button.png").is_file()
    or not (REPO / "references/crop/skip_button_skip_button.png").is_file(),
    reason="skip_button assets missing",
)
def test_skip_button_overlay_true_on_reference_png() -> None:
    img = cv2.imread(str(REPO / "references/skip_button.png"))
    assert img is not None
    doc = json.loads((REPO / "area.json").read_text(encoding="utf-8"))
    cfg = load_merged_analyze_yaml(REPO)
    overlay = cfg.get("overlay") or []
    out = evaluate_overlay_rules(img, doc, REPO, overlay)

    assert "skip_button.visible" in out
    row = out["skip_button.visible"]
    assert row.get("matched") is True
    assert row.get("score", 0) >= 0.85
