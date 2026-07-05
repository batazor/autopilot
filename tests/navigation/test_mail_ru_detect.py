"""Real-frame regression: the RU («Почта») mail family detects end-to-end.

Captured live from bs5/bs6 (RU builds) on 2026-07-06 during the autonomous
babysitting run. Three RU gaps were found and fixed against these frames; each
assertion pins one of them:

  1. The whole mail family was UNDETECTABLE on RU: every ``screen_verify`` rule
     required ``contains: Mail`` while the title reads «Почта». The bot claimed
     a tab, lost ``current_screen`` and looped ``mail.claim.starred``
     navigation_failed for hours. Fixed with ``contains: [Mail, Почта]``.
  2. Tab identity (``mail.starred`` etc.) rides ``tab_active`` — a programmatic
     HSV check (cream active tab), locale-independent by construction. Guarded
     here so a reskin that breaks the HSV thresholds shows up as a corpus
     failure, not a live strand.
  3. ``detectTabs`` red-dot pages drive the mail.claim.* pushes; the bs6 frame
     carries a red badge on «Альянс» while «Избранное» is active — exactly the
     rewardless-mail state that used to hot-loop before ``push_ttl``.

Both fixture frames show the SAME logical screen (mail.starred) with different
badge sets — cheap coverage of badge-position variance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures"
_BS5_FRAME = _FIXTURES / "mail_starred_ru_badges_alliance_reports.png"
_BS6_FRAME = _FIXTURES / "mail_starred_ru_badge_alliance.png"
_DEFEAT_TITLE0_CROP = _FIXTURES / "exploration_defeat_ru_upsell_title0_crop.png"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", [_BS5_FRAME, _BS6_FRAME], ids=["bs5", "bs6"])
async def test_ru_mail_starred_detects(ru_catalog, fixture: Path) -> None:
    import cv2

    from config.loader import get_settings
    from navigation.detector import ScreenDetector
    from ocr.client import OcrClient

    assert fixture.is_file(), f"fixture missing: {fixture}"
    image = cv2.imread(str(fixture))
    assert image is not None, f"failed to decode {fixture}"

    detector = ScreenDetector(OcrClient(get_settings()))
    node = await detector.detect_screen(image)
    assert str(node) == "mail.starred", f"expected mail.starred, got {node!r}"


@pytest.mark.integration
def test_ru_mail_tabs_red_dot_pages(ru_catalog_no_ocr) -> None:
    """detectTabs on the bs6 frame: starred active, red dot only on alliance.

    This is the exact input that drives the mail.claim.* pushes — the active
    tab's own page must NOT be re-pushed, and the alliance badge (a rewardless
    unread mail the claim can't clear) must surface as the only red-dot page.
    """
    import cv2

    from layout.tab_active_detector import is_tab_active_in_bbox_percent

    image = cv2.imread(str(_BS6_FRAME))
    assert image is not None

    # Tab bboxes verbatim from games/wos/mail/area.yaml (percent of 720x1280).
    tabs = {
        "wars": {"x": 0.0, "y": 5.3, "width": 22.2007722007722, "height": 4.5},
        "alliance": {"x": 22.586872586872587, "y": 5.3, "width": 17.3996138996139, "height": 4.5},
        "starred": {"x": 80.0, "y": 5.3, "width": 20.0, "height": 4.5},
    }
    active = {
        name: is_tab_active_in_bbox_percent(image, bbox) for name, bbox in tabs.items()
    }
    assert active["starred"] is True, f"starred must read active: {active}"
    assert active["alliance"] is False, f"alliance must read inactive: {active}"
    assert active["wars"] is False, f"wars must read inactive: {active}"


@pytest.mark.integration
def test_ru_defeat_upsell_title0_reads_defeat(ru_catalog) -> None:
    """The RU squad-fight defeat card's banner crop must OCR to «Поражение».

    The crop is the live-cut ``exploration.defeat.title0`` bbox [22,25,56,7] —
    the card variant with «Получить силу:» upsell rows draws its banner ~60px
    above both older probes, which read plate-edge garbage / the upsell header.
    This pin guards the third known vertical layout of the result card.
    """
    import shutil
    import subprocess

    from config.loader import get_settings

    assert _DEFEAT_TITLE0_CROP.is_file(), f"fixture missing: {_DEFEAT_TITLE0_CROP}"
    cmd = str(getattr(get_settings().ocr, "tesseract_cmd", "tesseract") or "tesseract")
    if not shutil.which(cmd):
        pytest.skip(f"tesseract not found: {cmd!r}")
    out = subprocess.run(
        [cmd, str(_DEFEAT_TITLE0_CROP), "stdout", "-l", "rus+eng", "--psm", "7"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).stdout.strip()
    assert "Поражение" in out, f"title0 crop OCR drifted: {out!r}"
