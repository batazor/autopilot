"""Real-frame regression: the «Белая мгла» Hunter's Hut screen maps to ``hunters_hut``.

bs5 sat on the Hunter's Hut building interior («Охотничий домик: Ур. 2», furniture
tabs) but the bot detected ``intel.fight`` — the building title OCR (``building.title``)
matched no ``hunters_hut`` ``contains`` token, so detection fell through to the loose
``intel.fight.view`` area-landmark catch-all.

Two coupled causes, both guarded here against the real captured frame:
  1. the live RU plate reads «Охотничий домик» (added to the rule's ``contains``);
  2. the white-outlined plate garbles under the default psm-6 pass — it only reads
     cleanly with ``preprocess: title_line`` (now carried on the screen-verify rule).

Integration-marked: needs Tesseract + ``rus`` traineddata (the RU build's OCR lang).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "hunters_hut_ru_belaya_mgla.png"


def _require_rus_ocr() -> None:
    from config.loader import get_settings
    from ocr.client import OcrClient

    settings = get_settings()
    cmd = str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract")
    if not shutil.which(cmd):
        pytest.skip(f"Tesseract executable not found: {cmd!r}")
    if "rus" not in OcrClient(settings)._available_langs(cmd):
        pytest.skip("rus traineddata not installed — required to read the RU build")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_belaya_mgla_hunters_hut_frame_detects_hunters_hut() -> None:
    import cv2

    from config.loader import get_settings
    from navigation.detector import ScreenDetector
    from navigation.screen_graph import invalidate_screen_verify_config
    from ocr.client import OcrClient
    from services import bind_active_module_catalog, get_active_module_catalog

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    _require_rus_ocr()

    image = cv2.imread(str(_FIXTURE))
    assert image is not None, f"failed to decode {_FIXTURE}"

    # The RU «Белая мгла» build binds the wos_ru catalog → OCR lang `rus`. Restore
    # the prior catalog afterwards so this never leaks into sibling tests.
    prior = get_active_module_catalog()
    try:
        bind_active_module_catalog("wos_ru")
        invalidate_screen_verify_config()
        ScreenDetector._landmark_rules_cache.clear()
        ScreenDetector._landmark_rules_cache_fp = None

        detector = ScreenDetector(OcrClient(get_settings()))

        # End-to-end: the real frame must resolve to the building screen, not the
        # loose intel.fight catch-all.
        node = await detector.detect_screen(image)
        assert str(node) == "hunters_hut", f"expected hunters_hut, got {node!r}"
    finally:
        bind_active_module_catalog(prior)
        invalidate_screen_verify_config()
        ScreenDetector._landmark_rules_cache.clear()
        ScreenDetector._landmark_rules_cache_fp = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_building_title_needs_title_line_for_ru_plate() -> None:
    """Pin WHY the fix is load-bearing: the white-outlined plate only yields a
    clean «Охотничий домик» substring under ``title_line`` — raw psm-6 garbles it.
    """
    import cv2

    from config.loader import get_settings
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc
    from layout.types import Region
    from ocr.client import OcrClient
    from services import bind_active_module_catalog, get_active_module_catalog

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    _require_rus_ocr()

    image = cv2.imread(str(_FIXTURE))
    h, w = int(image.shape[0]), int(image.shape[1])

    pair = screen_region_by_name(load_area_doc(_REPO_ROOT), "building.title")
    assert pair is not None, "merged area manifest has no `building.title` region"
    bbox = pair[1]["bbox"]
    region = Region(
        int(float(bbox["x"]) / 100.0 * w),
        int(float(bbox["y"]) / 100.0 * h),
        int(float(bbox["width"]) / 100.0 * w),
        int(float(bbox["height"]) / 100.0 * h),
    )

    prior = get_active_module_catalog()
    try:
        bind_active_module_catalog("wos_ru")
        ocr = OcrClient(get_settings())
        res = await ocr.ocr_region(image, region, preprocess="title_line")
        text = (res.text or "").strip().lower()
        print(f"building.title title_line text={res.text!r} conf={res.confidence:.3f}")
        # The contains-match the hunters_hut rule relies on (case-insensitive
        # substring) must be satisfiable from this read.
        assert "охотничий домик" in text, f"title_line read lost the plate: {text!r}"
    finally:
        bind_active_module_catalog(prior)
