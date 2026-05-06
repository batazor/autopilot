"""Auto-skip ads and entry-screen popups.

Logic mirrors internal/device/ad_skip.go:
  1. Scan the screen with OCR for known popup keywords (up to 30 s total).
  2. If "Confirm" found (white text / green bg) → tap welcome_back_continue_button.
  3. Otherwise → tap ad_banner_close and loop.
  4. Stop when no keywords detected or timeout reached.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from actions.tap import BotActions
from capture.window import QuartzCapture
from layout.types import Point, Region
from ocr.client import OcrClient, OCRResult
from ocr.fuzzy import match

logger = logging.getLogger(__name__)

# Keyword list from the Go reference — order matters (Confirm checked first)
_POPUP_KEYWORDS: list[str] = [
    "welcome",
    "alliance",
    "natalia",
    "exploration",
    "hero gear",
    "general speedup",
    "construction speedup",
    "resource",
    "mastery material",
    "purchase limit",
    "agility",
    "brothers in arms",
    "event coming soon",
    "dawn pack",
    "unyielding dawn",
    "overview",
    "confirm",
]

# Screen regions to scan for popup text (720×1280 layout)
_SCAN_REGIONS: list[Region] = [
    Region(100, 150, 520, 60),   # top banner title
    Region(200, 350, 320, 80),   # mid popup title
    Region(150, 800, 420, 70),   # confirm / ok button area
    Region(400, 180, 260, 60),   # top-right area
    Region(100, 500, 520, 120),  # center body
]

# Tap targets (720×1280 @ 320 DPI)
_AD_BANNER_CLOSE    = Point(660, 190)   # X button top-right of popup
_CONFIRM_BUTTON     = Point(360, 890)   # green Confirm / Continue button
_FALLBACK_CLOSE     = Point(360, 1150)  # bottom-center tap-anywhere area

_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL   = 1.0
_TAP_PAUSE       = 0.35


class AdSkipper:
    """Handles entry-screen ads and popup dismissal for one instance."""

    def __init__(self, instance_id: str) -> None:
        self._instance_id = instance_id
        self._actions = BotActions()
        self._capture = QuartzCapture()
        self._ocr = OcrClient()

    def _grab(self) -> np.ndarray:
        title = self._actions._get_window_title(self._instance_id)
        wid = self._capture.find_window(title)
        return self._capture.capture(wid)

    async def handle_entry_screens(self) -> None:
        """Dismiss all known popups / ads within a 30-second window."""
        logger.info("Ad-skip: watching entry screens on %s", self._instance_id)
        deadline = time.monotonic() + _TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            try:
                image = self._grab()
                results = await self._ocr.ocr_regions(image, _SCAN_REGIONS)
            except Exception:
                logger.exception("Ad-skip OCR failed, retrying")
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            detected = _collect_texts(results)
            if not detected:
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            logger.debug("Ad-skip detected text: %s", detected)

            if _has_confirm(detected):
                logger.info("Ad-skip: tapping Confirm button on %s", self._instance_id)
                self._actions.tap(self._instance_id, _CONFIRM_BUTTON)
                await asyncio.sleep(_TAP_PAUSE)
                return  # Confirm closes the entry flow

            if _has_popup(detected):
                logger.info("Ad-skip: closing popup on %s (text=%r)", self._instance_id, detected[0])
                self._actions.tap(self._instance_id, _AD_BANNER_CLOSE)
                await asyncio.sleep(_TAP_PAUSE)
                # Don't return — loop to catch next popup
            else:
                await asyncio.sleep(_POLL_INTERVAL)

        logger.info("Ad-skip: 30 s timeout, continuing on %s", self._instance_id)

    async def dismiss_once(self) -> bool:
        """Try to dismiss a single popup right now. Returns True if something was tapped."""
        try:
            image = self._grab()
            results = await self._ocr.ocr_regions(image, _SCAN_REGIONS)
        except Exception:
            logger.exception("Ad-skip dismiss_once OCR failed")
            return False

        detected = _collect_texts(results)
        if not detected:
            return False

        if _has_confirm(detected):
            self._actions.tap(self._instance_id, _CONFIRM_BUTTON)
        else:
            self._actions.tap(self._instance_id, _AD_BANNER_CLOSE)

        await asyncio.sleep(_TAP_PAUSE)
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_texts(results: list[OCRResult]) -> list[str]:
    """Return non-empty text strings from OCR results, lowercased."""
    return [r.text.strip().lower() for r in results if r.text.strip()]


def _has_confirm(texts: list[str]) -> bool:
    for text in texts:
        if match(text, ["confirm"], threshold=0.82):
            return True
    return False


def _has_popup(texts: list[str]) -> bool:
    non_confirm = [kw for kw in _POPUP_KEYWORDS if kw != "confirm"]
    for text in texts:
        if match(text, non_confirm, threshold=0.75):
            return True
    return False
