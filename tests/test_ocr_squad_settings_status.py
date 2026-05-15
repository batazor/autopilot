from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VICTORY_REF = _REPO_ROOT / "references" / "page.squad_settings.status.victory.png"
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
async def test_ocr_squad_settings_status_reads_victory() -> None:
    """Real-OCR check for `page.squad_settings.status` on the victory reference.

    The status text is yellow/orange on a light cream background — a known
    low-contrast case. This test guards that local Tesseract still reads it well
    enough for the `squad_fight` scenario's `victory|defeat` polling loop.
    """
    import cv2

    from layout.area_lookup import screen_region_by_name
    from layout.types import Region as LayoutRegion
    from ocr.client import OcrClient

    assert _VICTORY_REF.is_file(), f"reference image missing: {_VICTORY_REF}"
    assert _AREA_JSON.is_file(), f"area.json missing: {_AREA_JSON}"
    _assert_local_ocr_available()

    image = cv2.imread(str(_VICTORY_REF))
    assert image is not None, f"failed to decode {_VICTORY_REF}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    pair = screen_region_by_name(area_doc, "page.squad_settings.status")
    assert pair is not None, "area.json has no `page.squad_settings.status` region"
    region_def = pair[1]
    bbox = region_def.get("bbox")
    assert isinstance(bbox, dict), f"region missing bbox: {region_def!r}"

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0, f"degenerate pixel bbox: {(px, py, pw, ph)}"

    from config.loader import get_settings

    result = await OcrClient(get_settings()).ocr_region(image, LayoutRegion(px, py, pw, ph))
    text = str(getattr(result, "text", "") or "").strip()
    conf = float(getattr(result, "confidence", 0.0) or 0.0)

    diag = (
        f"text={text!r} confidence={conf:.4f} "
        f"pixel_bbox=(x={px}, y={py}, w={pw}, h={ph})"
    )
    print(f"OCR page.squad_settings.status victory: {diag}")

    assert text, f"OCR returned empty text for victory reference. {diag}"
    assert "victory" in text.lower(), (
        "OCR did not detect 'victory' substring in `page.squad_settings.status` "
        f"region. The yellow-on-cream Victory! banner may need preprocessing or "
        f"a tighter bbox. {diag}"
    )

    region_threshold = float(region_def.get("threshold", 0.9) or 0.9)
    assert conf >= region_threshold, (
        f"OCR confidence {conf:.4f} below region threshold {region_threshold:.2f}. "
        f"The squad_fight scenario overrides to 0.0 for polling, but the labelled "
        f"region expects {region_threshold:.2f}. {diag}"
    )
