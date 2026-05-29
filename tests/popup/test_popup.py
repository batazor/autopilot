"""Offline tests for the game-wide pop-up detector.

No emulator. Geometry and classification are covered deterministically with
synthetic frames (a sharp card over a blurred scrim); real fixtures cover the
negative (a normal, edge-everywhere screen) and the named validation shot when
present. Missing fixtures skip with a clear message rather than fail hard.
"""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore[import-untyped]
import numpy as np
import pytest

from layout.types import Region
from popup.classify import SafetyClassifier
from popup.detector import PopupDetector
from popup.mask import SharpnessMask
from popup.models import DetectionSignals, PopupKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# --- helpers ---------------------------------------------------------------


def _blurred_scrim(width: int, height: int) -> np.ndarray:
    """A heavily blurred, busy background: high luminance variance, ~no fine edges."""
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    # Strong blur removes the high-frequency content the detector keys on.
    return cv2.GaussianBlur(noise, (0, 0), sigmaX=12.0)


def _sharp_card(img: np.ndarray, bbox: Region, *, lines: list[str]) -> np.ndarray:
    """Paste a sharp white card with crisp black text into ``img`` at ``bbox``."""
    out = img.copy()
    x, y, w, h = bbox.x, bbox.y, bbox.w, bbox.h
    cv2.rectangle(out, (x, y), (x + w, y + h), (255, 255, 255), thickness=-1)
    cv2.rectangle(out, (x, y), (x + w, y + h), (20, 20, 20), thickness=3)
    # Crisp X in the top-right corner (fine edges → survives the sharpness mask).
    xc = x + int(w * 0.91)
    yc = y + int(h * 0.06)
    cv2.line(out, (xc - 14, yc - 14), (xc + 14, yc + 14), (10, 10, 10), 3)
    cv2.line(out, (xc - 14, yc + 14), (xc + 14, yc - 14), (10, 10, 10), 3)
    for i, line in enumerate(lines):
        cv2.putText(
            out,
            line,
            (x + int(w * 0.12), y + int(h * 0.4) + i * 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.4,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
    return out


def _modal_frame(width: int = 720, height: int = 1280, *, lines: list[str]) -> tuple[np.ndarray, Region]:
    bbox = Region(int(width * 0.18), int(height * 0.30), int(width * 0.64), int(height * 0.38))
    img = _sharp_card(_blurred_scrim(width, height), bbox, lines=lines)
    return img, bbox


# --- mask geometry ---------------------------------------------------------


def test_localize_recovers_centered_card() -> None:
    img, bbox = _modal_frame(lines=["Special Offer"])
    mask = SharpnessMask()
    loc = mask.localize(img)
    assert loc is not None
    _, found = loc
    # Card center recovered near true center (within 8% of frame on each axis).
    fcx = (found.x + found.w / 2) / img.shape[1]
    fcy = (found.y + found.h / 2) / img.shape[0]
    assert abs(fcx - 0.5) < 0.08
    assert abs(fcy - (bbox.y + bbox.h / 2) / img.shape[0]) < 0.10


def test_signals_flag_overlay_for_modal() -> None:
    img, _ = _modal_frame(lines=["Special Offer"])
    mask = SharpnessMask()
    loc = mask.localize(img)
    assert loc is not None
    m, bbox = loc
    signals = mask.compute_signals(m, bbox, img.shape)
    assert 0.10 <= signals.card_frac <= 0.90
    assert signals.scrim_sharp < 0.01
    assert signals.overlay_present is True


def test_close_region_is_top_right_slice() -> None:
    mask = SharpnessMask()
    bbox = Region(100, 200, 400, 300)
    cr = mask.close_region(bbox)
    # Right edge aligns with the card; sits in the top 12% and right ~18%.
    assert cr.x + cr.w == bbox.x + bbox.w
    assert cr.x > bbox.x + bbox.w * 0.5
    assert cr.y == bbox.y
    assert cr.h <= int(bbox.h * 0.12) + 1


def test_compute_signals_math_is_resolution_independent() -> None:
    mask = SharpnessMask()
    bbox = Region(0, 0, 360, 640)  # quarter of a 720×1280 frame
    blank = np.zeros((1280, 720), dtype=np.uint8)
    signals = mask.compute_signals(blank, bbox, (1280, 720))
    assert signals.card_frac == pytest.approx(0.25, abs=1e-6)
    assert signals.center == pytest.approx((0.25, 0.25))


# --- classifier (deterministic, no OCR) ------------------------------------


@pytest.fixture
def classifier() -> SafetyClassifier:
    return SafetyClassifier()


def _overlay_signals() -> DetectionSignals:
    return DetectionSignals(card_frac=0.5, center=(0.5, 0.5), scrim_sharp=0.0, overlay_present=True)


@pytest.mark.parametrize(
    ("text", "has_close", "expected"),
    [
        ("special pack $4.99 buy now", True, PopupKind.PURCHASE),
        ("monthly card 9.99 /mo subscribe", False, PopupKind.PURCHASE),
        ("verify you are not a robot", True, PopupKind.CAPTCHA),
        ("level up reward tap to claim", False, PopupKind.REWARD_CLAIM),
        ("daily login got it", False, PopupKind.SAFE_DISMISS),
        ("event splash", True, PopupKind.SAFE_DISMISS),
        ("mysterious notice", False, PopupKind.UNKNOWN_MODAL),
    ],
)
def test_classify(classifier: SafetyClassifier, text: str, has_close: bool, expected: PopupKind) -> None:
    assert classifier.classify(text, _overlay_signals(), has_close=has_close) == expected


def test_captcha_beats_purchase_and_close(classifier: SafetyClassifier) -> None:
    # Safety order: captcha is checked first even with a price and a close button.
    text = "verify to claim $4.99 reward"
    assert classifier.classify(text, _overlay_signals(), has_close=True) == PopupKind.CAPTCHA


def test_no_overlay_no_match_is_none(classifier: SafetyClassifier) -> None:
    quiet = DetectionSignals(card_frac=0.5, center=(0.5, 0.5), scrim_sharp=0.0, overlay_present=False)
    assert classifier.classify("nothing here", quiet, has_close=False) == PopupKind.NONE


# --- detector end-to-end ---------------------------------------------------


async def test_detector_localizes_and_points_at_close(ocr_client) -> None:
    img, _bbox = _modal_frame(lines=["Special Offer", "$4.99"])
    detector = PopupDetector(ocr_client)
    state = await detector.detect(img)

    assert state.signals.overlay_present is True
    assert 0.10 <= state.signals.card_frac <= 0.90
    assert state.signals.scrim_sharp < 0.01
    assert state.bbox is not None
    # Close point lands inside the top-right slice of the recovered bbox.
    cr = SharpnessMask().close_region(state.bbox)
    assert state.close_point is not None
    assert cr.x <= state.close_point.x <= cr.x + cr.w
    assert cr.y <= state.close_point.y <= cr.y + cr.h


async def test_detector_negative_on_real_screen(ocr_client) -> None:
    path = FIXTURES / "main_city_current_state.png"
    if not path.exists():
        pytest.skip(f"missing fixture: {path}")
    img = cv2.imread(str(path))
    assert img is not None
    detector = PopupDetector(ocr_client)
    state = await detector.detect(img)
    # A normal, edge-everywhere screen must not be treated as an actionable modal.
    assert state.kind in {PopupKind.NONE, PopupKind.AD_WEBVIEW}
    actionable = {PopupKind.SAFE_DISMISS, PopupKind.REWARD_CLAIM, PopupKind.PURCHASE, PopupKind.CAPTCHA}
    assert state.kind not in actionable


async def test_detector_charm_master_pack_if_present(ocr_client) -> None:
    # The validated reference shot — a real live "Charm Master Pack" purchase
    # modal captured at 720×1280. Geometry signals are OCR-independent and
    # asserted strictly; the *kind* depends on the OCR backend (the `$` glyph
    # in the price drives PURCHASE locally), so it is asserted as "an actionable
    # overlay modal" to stay robust across tesseract versions in CI.
    path = FIXTURES / "charm_master_pack.png"
    if not path.exists():
        pytest.skip(f"missing validated fixture: {path}")
    img = cv2.imread(str(path))
    assert img is not None
    detector = PopupDetector(ocr_client)
    state = await detector.detect(img)
    assert state.signals.overlay_present is True
    assert 0.40 <= state.signals.card_frac <= 0.70
    assert state.signals.center[0] == pytest.approx(0.50, abs=0.08)
    assert state.signals.scrim_sharp < 0.01
    assert state.kind in {PopupKind.PURCHASE, PopupKind.SAFE_DISMISS, PopupKind.UNKNOWN_MODAL}
    assert state.bbox is not None
    cr = SharpnessMask().close_region(state.bbox)
    assert state.close_point is not None
    assert cr.x <= state.close_point.x <= cr.x + cr.w
    assert cr.y <= state.close_point.y <= cr.y + cr.h
