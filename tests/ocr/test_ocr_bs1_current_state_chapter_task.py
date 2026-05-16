from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "bs1_current_state.png"
_AREA_JSON = _REPO_ROOT / "area.json"


def _assert_local_ocr_available() -> None:
    from config.loader import get_settings

    settings = get_settings()
    cmd = str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract")
    assert shutil.which(cmd), (
        f"Tesseract executable not found: {cmd!r}. "
        "Install Tesseract with eng.traineddata before running this test."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ocr_bs1_current_state_chapter_task_prints_text() -> None:
    """OCR snapshot for the current rolling screenshot fixture.

    This is intentionally tolerant: OCR text may vary as the game UI changes.
    """
    import cv2

    from layout.area_lookup import screen_region_by_name
    from layout.types import Region as LayoutRegion
    from ocr.client import OcrClient

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    assert _AREA_JSON.is_file(), f"area.json missing: {_AREA_JSON}"
    _assert_local_ocr_available()

    image = cv2.imread(str(_FIXTURE))
    assert image is not None, f"failed to decode {_FIXTURE}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    pair = screen_region_by_name(area_doc, "chapter.task")
    assert pair is not None, "area.json has no `chapter.task` region"
    bbox = pair[1].get("bbox")
    assert isinstance(bbox, dict)

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0

    from config.loader import get_settings

    result = await OcrClient(get_settings()).ocr_region(image, LayoutRegion(px, py, pw, ph))
    text = str(getattr(result, "text", "") or "").strip()
    conf = float(getattr(result, "confidence", 0.0) or 0.0)

    # Print for quick inspection in CI logs.
    print(f"OCR chapter.task fixture text={text!r} confidence={conf:.4f}")

    # Must not be empty (regression guard).
    assert text, f"empty OCR text (confidence={conf:.4f})"

