"""Real-frame regression: the «Белая мгла» Intel («Разведка») board works end-to-end.

Captured live from bs5 (com.gof.globalru, wos_ru catalog) on 2026-07-05 while
verifying the intel_run flow. Three RU gaps were found and fixed against this
frame; each assertion below pins one of them:

  1. ``intel.stamina`` read EMPTY under the base ``fast_line`` (raw psm-7) —
     the white outlined «9/70» digits on the bar gradient need contrast
     enhancement first. The wos_ru overlay now carries ``preprocess:
     enhance_line`` on the region (reads '9/70' @ conf ~0.97). Without the read
     the intel planner is stamina-blind and falls back to the deterministic
     pick instead of budgeting.
  2. ``intel.claim_all`` — the RU «Получить все» button is a GREEN pill; the EN
     'Claim All' template scores ~0.44 (gate 0.9), so passive rewards were
     never claimed on RU. The overlay now ships a live-cut RU crop.
  3. The board itself must detect as ``intel`` (RU «Разведка» styled-title
     template via the overlay) — guarded end-to-end through ScreenDetector.

Marker detection (pin colour/kind) is template/colour based and
locale-independent; the same frame carries 5 skull pins + 1 gold camp pin, so
it doubles as a detection regression for the RU board skin.

Integration-marked: needs Tesseract + ``rus`` traineddata.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "intel_board_ru_belaya_mgla.png"


def _require_rus_ocr() -> None:
    from config.loader import get_settings
    from ocr.client import OcrClient

    settings = get_settings()
    cmd = str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract")
    if not shutil.which(cmd):
        pytest.skip(f"Tesseract executable not found: {cmd!r}")
    if "rus" not in OcrClient(settings)._available_langs(cmd):
        pytest.skip("rus traineddata not installed — required to read the RU build")


def _bind_ru():
    from navigation.detector import ScreenDetector
    from navigation.screen_graph import invalidate_screen_verify_config
    from services import bind_active_module_catalog

    bind_active_module_catalog("wos_ru")
    invalidate_screen_verify_config()
    ScreenDetector._landmark_rules_cache.clear()
    ScreenDetector._landmark_rules_cache_fp = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_belaya_mgla_intel_board_detects_intel() -> None:
    import cv2

    from config.loader import get_settings
    from navigation.detector import ScreenDetector
    from ocr.client import OcrClient
    from services import get_active_module_catalog

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    _require_rus_ocr()

    image = cv2.imread(str(_FIXTURE))
    assert image is not None, f"failed to decode {_FIXTURE}"

    prior = get_active_module_catalog()
    try:
        _bind_ru()
        detector = ScreenDetector(OcrClient(get_settings()))
        node = await detector.detect_screen(image)
        assert str(node) == "intel", f"expected intel, got {node!r}"
    finally:
        from services import bind_active_module_catalog

        bind_active_module_catalog(prior)
        _rules_reset()


def _rules_reset() -> None:
    from navigation.detector import ScreenDetector
    from navigation.screen_graph import invalidate_screen_verify_config

    invalidate_screen_verify_config()
    ScreenDetector._landmark_rules_cache.clear()
    ScreenDetector._landmark_rules_cache_fp = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intel_stamina_ru_needs_enhance_line() -> None:
    """The stamina read the planner budgets on: «9/70» via the overlay region."""
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

    prior = get_active_module_catalog()
    try:
        bind_active_module_catalog("wos_ru")
        pair = screen_region_by_name(load_area_doc(_REPO_ROOT), "intel.stamina")
        assert pair is not None, "merged area manifest has no `intel.stamina` region"
        region_def = pair[1]
        # The overlay must win the merge — the base fast_line region reads ''.
        assert region_def.get("preprocess") == "enhance_line", (
            f"wos_ru intel.stamina override lost the merge: {region_def.get('preprocess')!r}"
        )
        bbox = region_def["bbox"]
        region = Region(
            int(float(bbox["x"]) / 100.0 * w),
            int(float(bbox["y"]) / 100.0 * h),
            int(float(bbox["width"]) / 100.0 * w),
            int(float(bbox["height"]) / 100.0 * h),
        )
        res = await OcrClient(get_settings()).ocr_region(
            image, region, region_id="intel.stamina", preprocess="enhance_line"
        )

        from games.wos.intel.state import parse_stamina

        parsed = parse_stamina(res.text)
        assert parsed == (9, 70), (
            f"stamina read drifted: text={res.text!r} conf={res.confidence:.3f} parsed={parsed}"
        )
    finally:
        bind_active_module_catalog(prior)


@pytest.mark.integration
def test_intel_claim_all_ru_crop_matches_board() -> None:
    """The RU «Получить все» crop must clear the 0.9 gate inside its region."""
    import cv2

    from services import bind_active_module_catalog, get_active_module_catalog

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"

    image = cv2.imread(str(_FIXTURE))
    h, w = int(image.shape[0]), int(image.shape[1])

    prior = get_active_module_catalog()
    try:
        bind_active_module_catalog("wos_ru")
        from layout.area_lookup import screen_region_by_name
        from layout.area_manifest import load_area_doc

        pair = screen_region_by_name(load_area_doc(_REPO_ROOT), "intel.claim_all")
        assert pair is not None
        region_def = pair[1]
        assert region_def.get("isSearch") is True, "wos_ru claim_all override lost the merge"
        bbox = region_def["bbox"]
        x = int(float(bbox["x"]) / 100.0 * w)
        y = int(float(bbox["y"]) / 100.0 * h)
        ww = int(float(bbox["width"]) / 100.0 * w)
        hh = int(float(bbox["height"]) / 100.0 * h)

        crop = cv2.imread(
            str(_REPO_ROOT / "games/wos/ru/intel/references/crop/intel_intel.claim_all.png")
        )
        assert crop is not None, "RU claim_all crop missing"
        roi = image[y : y + hh, x : x + ww]
        res = cv2.matchTemplate(
            cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
            cv2.TM_CCOEFF_NORMED,
        )
        score = float(cv2.minMaxLoc(res)[1])
        assert score >= float(region_def.get("threshold", 0.9)), (
            f"RU claim_all crop under gate on its own board frame: {score:.3f}"
        )
    finally:
        bind_active_module_catalog(prior)


def test_intel_markers_detect_on_ru_board() -> None:
    """Pin detection is locale-independent — the RU board must still yield the
    5 skull pins + the gold camp («сбор»/rescue) pin present on the capture."""
    import cv2
    from games.wos.intel.detection import detect_intel_markers

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    image = cv2.imread(str(_FIXTURE))

    markers = detect_intel_markers(image, threshold=0.72, nms_distance_px=40)
    kinds = sorted(m.kind for m in markers)
    assert kinds.count("skull") == 5, f"expected 5 skull pins, got {kinds}"
    assert kinds.count("camp") == 1, f"expected 1 camp pin, got {kinds}"
    camp = next(m for m in markers if m.kind == "camp")
    assert camp.color == "gold", f"camp pin colour drifted: {camp.color}"
