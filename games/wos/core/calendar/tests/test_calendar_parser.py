"""Tests for the event-detail popup parser.

Date parsing (the critical, deterministic output) is unit-tested directly. The
full pipeline runs against the two saved popup fixtures through the real
``OcrClient`` — skipped if Tesseract isn't installed — and asserts the parsed
start/end (exact) plus a loose name match (OCR text varies by version).
"""
from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import cv2
import pytest
from games.wos.core.calendar import parser

_SAMPLES = Path(__file__).resolve().parent.parent / "references" / "samples"


def test_parse_date_range_basic():
    rng = parser.parse_date_range("2026 06 12 00 00 2026 06 13 24 00")
    assert rng == (
        datetime(2026, 6, 12, tzinfo=UTC),
        datetime(2026, 6, 14, tzinfo=UTC),   # 06-13 24:00 → 06-14 00:00
    )


def test_parse_date_range_repairs_digit_confusion():
    # Tesseract misreads 8→B / 0→O on the date line; the fixup recovers them.
    rng = parser.parse_date_range("2026 06 OB 00 00 2026 06 14 24 00")
    assert rng[0] == datetime(2026, 6, 8, tzinfo=UTC)


def test_parse_date_range_rejects_short():
    assert parser.parse_date_range("2026 06 12") is None


def test_clean_event_name_strips_leading_badge_noise():
    assert parser.clean_event_name("x Foundry Battle") == "Foundry Battle"
    assert parser.clean_event_name("1 Vault of Enigma") == "Vault of Enigma"
    assert parser.clean_event_name("2  Fortress Battles") == "Fortress Battles"
    # clean names are left untouched
    assert parser.clean_event_name("Treasure Hunt Drill") == "Treasure Hunt Drill"
    assert parser.clean_event_name("Hero Rally") == "Hero Rally"
    assert parser.clean_event_name("Crazy Joe") == "Crazy Joe"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
@pytest.mark.parametrize(
    ("fixture", "name_substr", "start", "end"),
    [
        ("event_detail_popup.png", "Fortune",
         datetime(2026, 6, 12, tzinfo=UTC), datetime(2026, 6, 14, tzinfo=UTC)),
        ("event_detail_popup_2.png", "Alliance",
         datetime(2026, 6, 8, tzinfo=UTC), datetime(2026, 6, 15, tzinfo=UTC)),
        # Top-row event: its popup opens high on screen (card y < 250) — must
        # still be detected (regression for the _CARD_MIN_Y gate).
        ("event_detail_popup_3_high.png", "Foundry",
         datetime(2026, 6, 8, tzinfo=UTC), datetime(2026, 6, 15, tzinfo=UTC)),
    ],
)
def test_parse_popup_fixtures(fixture, name_substr, start, end):
    from config.loader import load_settings
    from ocr.client import OcrClient

    img = cv2.imread(str(_SAMPLES / fixture))
    assert img is not None, f"missing fixture {fixture}"
    client = OcrClient(load_settings())
    event = parser.parse_popup(img, client._run_tesseract)
    assert event is not None
    assert name_substr.lower() in event.name.lower()
    assert event.starts_at == start
    assert event.ends_at == end


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_find_card_bbox_locates_popup():
    img = cv2.imread(str(_SAMPLES / "event_detail_popup.png"))
    bbox = parser.find_card_bbox(img)
    assert bbox is not None
    _x, y, w, h = bbox
    assert w >= 330 and h >= 180 and y >= 250  # card-sized, below the tab strip


def test_detect_event_bars_candidates():
    img = cv2.imread(str(_SAMPLES / "calendar_0_top.png"))
    if img is None:
        pytest.skip("fixture missing")
    pts = parser.detect_event_bars(img)
    assert len(pts) >= 5
    ys = [y for _x, y in pts]
    assert ys == sorted(ys)                       # ordered top-to-bottom
    assert all(315 <= y <= 1175 for y in ys)      # within the scrollable event area
    assert all(0 <= x < img.shape[1] for x, _y in pts)


def test_find_go_button_located_and_centered():
    img = cv2.imread(str(_SAMPLES / "event_detail_popup_2.png"))
    if img is None:
        pytest.skip("fixture missing")
    go = parser.find_go_button(img)
    assert go is not None
    x, y = go
    assert 200 <= x <= 300        # horizontally centered in the card
    assert y > 400                # lower part of the popup


def test_find_card_bbox_none_on_plain_frame():
    # a calendar frame with no popup has no large white card
    img = cv2.imread(str(_SAMPLES / "calendar_0_top.png"))
    if img is None:
        pytest.skip("fixture missing")
    # (the active tab capsule is white but smaller than the card threshold)
    bbox = parser.find_card_bbox(img)
    assert bbox is None or bbox[3] < 180 or bbox[1] < 250
