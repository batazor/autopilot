"""``PopupDetector`` — orchestrates mask → signals → classify for one frame.

Intended to run as the pop-up safety gate in the perception pipeline. It starts
with modal geometry, then asks the normal screen analyzer to veto known pages
before any OCR-based pop-up action is chosen.

Two corroboration hooks for the caller:

- A full-bleed sharp frame with no blurred scrim is routed to the (stubbed)
  learned close-button model — that's the ad / webview tail the heuristic can't
  cover.
- :meth:`corroborates_unknown_screen` lets the pipeline combine
  ``overlay_present`` with a prior "screen == UNKNOWN" read into a stronger
  modal vote, which matters when the X locator misses (ads/webviews).

OCR is run on the bbox crop only — never the full frame — which is both faster
and kills background false positives.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Point
from popup.classify import SafetyClassifier
from popup.close_model import CloseButtonModel
from popup.mask import SharpnessMask
from popup.models import DetectionSignals, PopupKind, PopupState

if TYPE_CHECKING:
    from layout.types import Region
    from ocr.client import OcrClient


class ScreenAnalyzer(Protocol):
    async def detect_screen(
        self,
        image: np.ndarray,
        *,
        hint: object | None = None,
        expected: object | None = None,
    ) -> object: ...


# A frame that is almost entirely "sharp" with no clean blurred ring is not a
# native modal — it is a full-bleed ad / webview. Routed to the model fallback.
_FULL_BLEED_FRAC = 0.95
_PAGE_SCREEN_EXCLUDE = frozenset({"", "unknown", "none", "main_city"})
_CLOSE_SEARCH_LEFT_PAD_FRAC = 0.15
_CLOSE_SEARCH_RIGHT_PAD_FRAC = 0.60
_CLOSE_SEARCH_Y_PAD_FRAC = 0.15
_CLOSE_MIN_SIDE_FRAC = 0.18
_CLOSE_MAX_SIDE_FRAC = 0.60
_CLOSE_MIN_AREA_FRAC = 0.012
_CLOSE_MIN_FILL = 0.10
_CLOSE_MAX_FILL = 0.75
_CLOSE_MIN_ASPECT = 0.55
_CLOSE_MAX_ASPECT = 1.80

logger = logging.getLogger(__name__)


def _empty_signals() -> DetectionSignals:
    return DetectionSignals(card_frac=0.0, center=(0.0, 0.0), scrim_sharp=0.0, overlay_present=False)


def _no_popup(signals: DetectionSignals | None = None) -> PopupState:
    return PopupState(
        kind=PopupKind.NONE,
        bbox=None,
        close_point=None,
        primary_point=None,
        card_text="",
        signals=signals or _empty_signals(),
    )


def _page(screen_name: str, *, bbox: Region, signals: DetectionSignals) -> PopupState:
    return PopupState(
        kind=PopupKind.PAGE,
        bbox=bbox,
        close_point=None,
        primary_point=None,
        card_text="",
        signals=signals,
        screen_name=screen_name,
    )


class PopupDetector:
    """Detect and classify a pop-up over any screen, template-free."""

    def __init__(
        self,
        ocr_client: OcrClient,
        *,
        mask: SharpnessMask | None = None,
        classifier: SafetyClassifier | None = None,
        close_model: CloseButtonModel | None = None,
        screen_analyzer: ScreenAnalyzer | None = None,
    ) -> None:
        self._ocr = ocr_client
        self._mask = mask or SharpnessMask()
        self._classifier = classifier or SafetyClassifier()
        self._close_model = close_model or CloseButtonModel()
        if screen_analyzer is None:
            from navigation.detector import ScreenDetector

            screen_analyzer = ScreenDetector(ocr_client)
        self._screen_analyzer = screen_analyzer

    async def detect(self, image: np.ndarray) -> PopupState:
        """Run one detection pass over ``image`` (BGR) and return a state."""
        loc = self._mask.localize(image)
        if loc is None:
            return _no_popup()

        mask, bbox = loc
        signals = self._mask.compute_signals(mask, bbox, image.shape)
        screen_name = await self._detect_page_screen(image)
        if screen_name is not None:
            return _page(screen_name, bbox=bbox, signals=signals)

        # Full-bleed with no blurred scrim → likely ad/webview, hand to model.
        if signals.card_frac > _FULL_BLEED_FRAC and signals.scrim_sharp >= self._mask.config.scrim_max:
            return await self._model_fallback(image, bbox, signals)

        if not signals.overlay_present:
            return _no_popup(signals)

        close_region = self._mask.close_region(bbox)
        detected_close = await self._find_close(image, close_region)
        # Geometric fallback for native modals when the explicit X locator misses.
        close_point = detected_close or self._fallback_close_point(close_region)

        text = (await self._ocr_card(image, bbox)).lower()
        kind = self._classifier.classify(text, signals, has_close=detected_close is not None)
        if kind == PopupKind.REWARD_CLAIM:
            primary_point = self._find_primary(bbox)
        elif kind == PopupKind.TAP_TO_CONTINUE:
            primary_point = self._find_continue_point(bbox)
        else:
            primary_point = None

        return PopupState(
            kind=kind,
            bbox=bbox,
            close_point=close_point,
            primary_point=primary_point,
            card_text=text,
            signals=signals,
        )

    def corroborates_unknown_screen(self, state: PopupState, *, screen_is_unknown: bool) -> bool:
        """Combine ``overlay_present`` with a prior UNKNOWN-screen read.

        ``overlay_present AND screen == UNKNOWN`` is a strong modal vote even
        when the close locator misses (ads/webviews), so the pipeline can treat
        an ``UNKNOWN_MODAL`` / ``AD_WEBVIEW`` state as a confirmed block.
        """
        return (
            state.kind not in {PopupKind.NONE, PopupKind.PAGE}
            and state.signals.overlay_present
            and screen_is_unknown
        )

    async def _detect_page_screen(self, image: np.ndarray) -> str | None:
        """Return a known page/screen id, or ``None`` when it still looks unknown.

        The sharpness mask only answers "does this frame look modal-like?".
        Welcome/event/ad pages can share those geometry signals, so corroborate
        with the normal screen analyzer before OCR safety classification.
        """
        try:
            detected = await self._screen_analyzer.detect_screen(image)
        except Exception:
            logger.debug("popup: screen analyzer failed", exc_info=True)
            return None
        screen_name = str(getattr(detected, "value", detected) or "").strip()
        if screen_name.lower() in _PAGE_SCREEN_EXCLUDE:
            return None
        return screen_name

    async def _model_fallback(
        self,
        image: np.ndarray,
        bbox: Region,
        signals: DetectionSignals,
    ) -> PopupState:
        """Ad / webview path: use the learned model if its weights are present."""
        close_point: Point | None = None
        if self._close_model.available():
            close_point = await self._close_model.find(image)
        return PopupState(
            kind=PopupKind.AD_WEBVIEW,
            bbox=bbox,
            close_point=close_point,
            primary_point=None,
            card_text="",
            signals=signals,
        )

    async def _find_close(self, image: np.ndarray, close_region: Region) -> Point | None:
        """Locate an explicit close button in the top-right slice.

        A small image-processing locator handles native modal X buttons: it
        looks for a compact, roughly square bright or dark component in an
        expanded top-right search box. The expansion matters for ad popups whose
        sharpness bbox clips the protruding close button by a few pixels.

        The learned model remains the fallback for full-bleed/webview variants.
        Returns ``None`` when no explicit X-like component is found — the caller
        applies a conservative geometric tap fallback.
        """
        found_by_pixels = self._find_close_by_pixels(image, close_region)
        if found_by_pixels is not None:
            return found_by_pixels
        if not self._close_model.available():
            return None
        crop = image[
            close_region.y : close_region.y + close_region.h,
            close_region.x : close_region.x + close_region.w,
        ]
        found = await self._close_model.find(crop)
        if found is None:
            return None
        return found.offset(close_region.x, close_region.y)

    def _find_close_by_pixels(self, image: np.ndarray, close_region: Region) -> Point | None:
        """Detect a generic X close button in the native-modal close region.

        The detector deliberately avoids popup-specific templates. It thresholds
        bright low-saturation strokes (white X buttons) and dark strokes (black X
        buttons in tests / plain dialogs), filters for square-ish components,
        then picks the best right-biased candidate.
        """
        x1, y1, x2, y2 = self._expanded_close_search_bounds(image, close_region)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        masks = (
            ((hsv[:, :, 1] <= 90) & (hsv[:, :, 2] >= 145)),
            (hsv[:, :, 2] <= 95),
        )

        candidates: list[tuple[float, Point]] = []
        for raw_mask in masks:
            mask = raw_mask.astype(np.uint8) * 255
            mask = cv2.morphologyEx(
                mask,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            )
            candidates.extend(self._close_candidates_from_mask(mask, origin=(x1, y1)))

        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _expanded_close_search_bounds(
        self,
        image: np.ndarray,
        close_region: Region,
    ) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        pad_left = int(close_region.w * _CLOSE_SEARCH_LEFT_PAD_FRAC)
        pad_right = int(close_region.w * _CLOSE_SEARCH_RIGHT_PAD_FRAC)
        pad_y = int(close_region.h * _CLOSE_SEARCH_Y_PAD_FRAC)
        x1 = max(0, close_region.x - pad_left)
        y1 = max(0, close_region.y - pad_y)
        x2 = min(width, close_region.x + close_region.w + pad_right)
        y2 = min(height, close_region.y + close_region.h + pad_y)
        return x1, y1, x2, y2

    def _close_candidates_from_mask(
        self,
        mask: np.ndarray,
        *,
        origin: tuple[int, int],
    ) -> list[tuple[float, Point]]:
        search_h, search_w = mask.shape[:2]
        min_dim = max(1, min(search_w, search_h))
        min_side = max(8, int(min_dim * _CLOSE_MIN_SIDE_FRAC))
        max_side = max(min_side + 1, int(min_dim * _CLOSE_MAX_SIDE_FRAC))
        min_area = max(24, int(min_dim * min_dim * _CLOSE_MIN_AREA_FRAC))

        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        out: list[tuple[float, Point]] = []
        ox, oy = origin
        for idx in range(1, count):
            area = int(stats[idx, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            x = int(stats[idx, cv2.CC_STAT_LEFT])
            y = int(stats[idx, cv2.CC_STAT_TOP])
            w = int(stats[idx, cv2.CC_STAT_WIDTH])
            h = int(stats[idx, cv2.CC_STAT_HEIGHT])
            if w < min_side or h < min_side or w > max_side or h > max_side:
                continue
            aspect = w / float(h)
            if not (_CLOSE_MIN_ASPECT <= aspect <= _CLOSE_MAX_ASPECT):
                continue
            fill = area / float(w * h)
            if not (_CLOSE_MIN_FILL <= fill <= _CLOSE_MAX_FILL):
                continue

            cx, cy = centroids[idx]
            right_bias = float(cx) / float(max(1, search_w))
            square_score = 1.0 - min(1.0, abs(1.0 - aspect))
            vertical_score = 1.0 - min(1.0, abs(float(cy) / float(max(1, search_h)) - 0.45) * 2.0)
            fill_score = 1.0 - min(1.0, abs(fill - 0.35) * 2.0)
            edge_penalty = 0.0
            if x <= 1:
                edge_penalty += 0.35
            if y <= 1 or y + h >= search_h - 1:
                edge_penalty += 0.25

            score = 2.0 * right_bias + square_score + 0.5 * vertical_score + 0.25 * fill_score - edge_penalty
            out.append((score, Point(int(round(ox + cx)), int(round(oy + cy)))))
        return out

    def _fallback_close_point(self, close_region: Region) -> Point:
        """Conservative geometric fallback for native modal close buttons.

        Real WOS ad close buttons tend to sit nearer the right edge of the
        top-right slice than its exact center, especially when the decorative
        header overhangs the left side of the localized card.
        """
        return Point(
            close_region.x + int(round(close_region.w * 0.86)),
            close_region.y + close_region.h // 2,
        )

    def _find_primary(self, bbox: Region) -> Point:
        """Best-effort CTA point for a reward card: lower-center of the card.

        Claim / Collect buttons sit near the bottom-center of reward modals.
        Geometric, resolution-independent, and only ever used for
        ``REWARD_CLAIM`` (never for purchases).
        """
        cx = bbox.x + bbox.w // 2
        cy = bbox.y + int(bbox.h * 0.85)
        return Point(cx, cy)

    def _find_continue_point(self, bbox: Region) -> Point:
        """Tap target for a "tap anywhere / tap to continue" page: the center.

        These pages carry no close button and advance on a tap *anywhere* on the
        card, so the geometric center is the safest, most reliable target — never
        the top-right corner where ``close_region`` would otherwise point.
        """
        return Point(bbox.x + bbox.w // 2, bbox.y + bbox.h // 2)

    async def _ocr_card(self, image: np.ndarray, bbox: Region) -> str:
        """OCR only the modal bbox crop. Never the full frame."""
        result = await self._ocr.ocr_region(image, bbox, region_id="popup_card")
        return result.text or ""
