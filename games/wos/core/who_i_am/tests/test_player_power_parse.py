"""End-to-end OCR check for the identity fields ``who_i_am`` reads off chief_profile.

The ``who_i_am`` scenario OCRs ``player.state`` / ``player.id`` on the
chief_profile screen. If either pipeline drifts — wrong bbox, wrong preprocess
backend, parser choking on punctuation — the bootstrap silently fails and no
player-scoped scenario runs.

This test pins the contract on the labelled ``chief_profile.png`` reference
shipped with the module. It is intentionally NOT auto-skipped: a missing
reference, a missing area.yaml entry, or an unreachable OCR backend are all
hard failures, because they break the production ``who_i_am`` flow.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import pytest

from config.loader import get_settings
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Region as LayoutRegion
from ocr.client import OcrClient
from ocr.preprocess import resolve_preprocess
from tasks.dsl_ocr_mixin import parse_ocr_integer

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCE_IMAGE = MODULE_DIR / "references" / "chief_profile.png"

# Ground truth read straight off the labelled crop files under references/crop/
# (``chief_profile_player.{power,state,id}.png``).
EXPECTED: dict[str, int] = {
    "player.state": 4_353,
    "player.id": 765_502_864,
}


def _assert_local_ocr_available() -> None:
    cmd = str(getattr(get_settings().ocr, "tesseract_cmd", "tesseract") or "tesseract")
    assert shutil.which(cmd), (
        f"Tesseract executable not found: {cmd!r}. "
        "Install Tesseract with eng.traineddata before running this test."
    )


async def _ocr_field(region_name: str) -> tuple[int | None, str, float]:
    assert REFERENCE_IMAGE.is_file(), f"reference image missing: {REFERENCE_IMAGE}"
    _assert_local_ocr_available()

    image = cv2.imread(str(REFERENCE_IMAGE))
    assert image is not None, f"failed to decode {REFERENCE_IMAGE}"
    h, w = int(image.shape[0]), int(image.shape[1])

    area_doc = load_area_doc(REPO_ROOT)
    pair = screen_region_by_name(area_doc, region_name)
    assert pair is not None, f"area.yaml has no `{region_name}` region"
    region_def = pair[1]
    bbox = region_def.get("bbox")
    assert isinstance(bbox, dict), f"`{region_name}` region missing a bbox: {region_def!r}"

    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    assert pw > 0 and ph > 0, f"degenerate pixel bbox for {region_name}: {(px, py, pw, ph)}"

    # Mirror production resolution: ``type: integer`` regions use Tesseract
    # ``fast_line`` unless the area/step overrides it.
    preprocess = resolve_preprocess(
        explicit=region_def.get("preprocess"),
        type_hint=region_def.get("type"),
    )
    digit_count = region_def.get("digit_count")
    result = await OcrClient(get_settings()).ocr_region(
        image,
        LayoutRegion(px, py, pw, ph),
        region_id=region_name,
        preprocess=preprocess,
        digit_count=int(digit_count) if digit_count else None,
    )
    return parse_ocr_integer(result.text or ""), result.text or "", float(result.confidence)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("region_name", list(EXPECTED.keys()))
async def test_who_i_am_fields_match_labelled_reference(region_name: str) -> None:
    parsed, raw_text, confidence = await _ocr_field(region_name)
    expected = EXPECTED[region_name]
    assert parsed == expected, (
        f"OCR did not match labelled `{region_name}` on chief_profile.png. "
        f"expected={expected} parsed={parsed} text={raw_text!r} confidence={confidence:.4f}"
    )
    assert confidence >= 0.5, (
        f"OCR confidence too low for `{region_name}`: {confidence:.4f} (text={raw_text!r})"
    )
