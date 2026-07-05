"""Real-frame regression: the «Белая мгла» 7-Day Login popup is fully handled on RU.

bs1 sat on the RU 7-Day Login event («Ежедневный подарок за вход», hero «Молли»,
green «Получить» claim button) but the bot detected ``unknown`` and burned ~15 min
looping ``dismiss_unknown_popup`` (the stuck-task watchdog aborted it at 900s),
never claiming. Two coupled gaps, both guarded here against the real captured frame:

  1. screen detection — the EN landmark is the hero-name plate «Molly»; on RU it
     reads «Молли». The wos_ru overlay (games/wos/ru/events/7-day) re-crops the
     ``event.7-day`` landmark so the popup detects as ``event.7-day`` (not unknown).
  2. the claim button — ``button.claim.big`` is the EN «Claim» template, which never
     matches the RU «Получить». The same overlay overrides ``button.claim.big`` with
     the «Получить» crop (region-name override → applies to every RU claim flow).

The detection landmark + button are TEMPLATES (findIcon), so this needs no OCR lang
— unlike the OCR-driven RU building-title fixes. detect_screen still runs sibling
OCR rules, so it uses ``ru_catalog`` (gated on rus); the button match is pure
findIcon and uses ``ru_catalog_no_ocr`` to run unconditionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "event_7day_ru_belaya_mgla.png"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_belaya_mgla_7day_frame_detects_event_7day(ru_catalog) -> None:
    import cv2

    from config.loader import get_settings
    from navigation.detector import ScreenDetector
    from ocr.client import OcrClient

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    image = cv2.imread(str(_FIXTURE))
    assert image is not None, f"failed to decode {_FIXTURE}"

    detector = ScreenDetector(OcrClient(get_settings()))
    node = await detector.detect_screen(image)
    assert str(node) == "event.7-day", f"expected event.7-day, got {node!r}"


@pytest.mark.asyncio
async def test_belaya_mgla_claim_button_matches_poluchit(ru_catalog_no_ocr) -> None:
    """The RU overlay makes ``button.claim.big`` match the green «Получить» button
    (region-name override). Pure findIcon — no OCR lang needed.
    """
    import cv2

    from layout.area_manifest import load_area_doc
    from tasks.dsl_scenario import evaluate_overlay_rules_async

    assert _FIXTURE.is_file(), f"fixture missing: {_FIXTURE}"
    image = cv2.imread(str(_FIXTURE))
    assert image is not None

    area_doc = load_area_doc(_REPO_ROOT, game="wos_ru")
    rule = {
        "name": "chk.claim",
        "region": "button.claim.big",
        "action": "exist",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        image, area_doc, _REPO_ROOT, [rule], current_screen="event.7-day"
    )
    row = out.get("chk.claim") or {}
    assert row.get("matched") is True, f"button.claim.big did not match «Получить»: {row!r}"
    # tap centre lands on the «Получить» button (lower-centre of the panel).
    assert 40.0 < float(row.get("tap_x_pct") or 0) < 60.0
    assert 78.0 < float(row.get("tap_y_pct") or 0) < 88.0
