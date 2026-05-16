from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAIN_CITY_REF = _REPO_ROOT / "references" / "main_city.png"
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
async def test_ocr_main_city_chapter_task_against_real_tesseract() -> None:
    """Real-OCR sanity check for `chapter.task` on `references/main_city.png`.

    The labelled region should contain a single line like:
      "Chapter 1 A Place to Call Home"
    """
    import cv2  # heavy import

    from layout.area_lookup import screen_region_by_name
    from layout.types import Region as LayoutRegion
    from ocr.client import OcrClient

    assert _MAIN_CITY_REF.is_file(), f"reference image missing: {_MAIN_CITY_REF}"
    assert _AREA_JSON.is_file(), f"area.json missing: {_AREA_JSON}"
    _assert_local_ocr_available()

    image = cv2.imread(str(_MAIN_CITY_REF))
    assert image is not None, f"failed to decode {_MAIN_CITY_REF}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    pair = screen_region_by_name(area_doc, "chapter.task")
    assert pair is not None, "area.json has no `chapter.task` region"
    bbox = pair[1].get("bbox")
    assert isinstance(bbox, dict), f"`chapter.task` region is missing a bbox: {pair[1]!r}"

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0, f"degenerate pixel bbox: {(px, py, pw, ph)}"

    from config.loader import get_settings

    result = await OcrClient(get_settings()).ocr_region(image, LayoutRegion(px, py, pw, ph))
    text = str(getattr(result, "text", "") or "").strip()
    conf = float(getattr(result, "confidence", 0.0) or 0.0)

    assert text == "Chapter 1 A Place to Call Home", (
        "OCR did not match the expected `chapter.task` text on main_city.png. "
        f"text={text!r} confidence={conf:.4f} pixel_bbox=(x={px}, y={py}, w={pw}, h={ph})"
    )
    assert conf > 0.0, f"OCR confidence is zero: text={text!r}"

