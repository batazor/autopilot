"""``PopupDetector`` — orchestrates mask → signals → classify for one frame.

Intended to run **unconditionally, before** screen detection in the perception
pipeline and short-circuit it when a modal is present: a modal occludes the
landmarks screen detection relies on, so detecting the overlay first avoids a
misread.

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

from typing import TYPE_CHECKING

from layout.types import Point
from popup.classify import SafetyClassifier
from popup.close_model import CloseButtonModel
from popup.mask import SharpnessMask
from popup.models import DetectionSignals, PopupKind, PopupState

if TYPE_CHECKING:
    import numpy as np

    from layout.types import Region
    from ocr.client import OcrClient

# A frame that is almost entirely "sharp" with no clean blurred ring is not a
# native modal — it is a full-bleed ad / webview. Routed to the model fallback.
_FULL_BLEED_FRAC = 0.95


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


class PopupDetector:
    """Detect and classify a pop-up over any screen, template-free."""

    def __init__(
        self,
        ocr_client: OcrClient,
        *,
        mask: SharpnessMask | None = None,
        classifier: SafetyClassifier | None = None,
        close_model: CloseButtonModel | None = None,
    ) -> None:
        self._ocr = ocr_client
        self._mask = mask or SharpnessMask()
        self._classifier = classifier or SafetyClassifier()
        self._close_model = close_model or CloseButtonModel()

    async def detect(self, image: np.ndarray) -> PopupState:
        """Run one detection pass over ``image`` (BGR) and return a state."""
        loc = self._mask.localize(image)
        if loc is None:
            return _no_popup()

        mask, bbox = loc
        signals = self._mask.compute_signals(mask, bbox, image.shape)

        # Full-bleed with no blurred scrim → likely ad/webview, hand to model.
        if signals.card_frac > _FULL_BLEED_FRAC and signals.scrim_sharp >= self._mask.config.scrim_max:
            return await self._model_fallback(image, bbox, signals)

        if not signals.overlay_present:
            return _no_popup(signals)

        close_region = self._mask.close_region(bbox)
        detected_close = await self._find_close(image, close_region)
        # Geometric fallback: a native modal's X is the top-right slice center.
        close_point = detected_close or close_region.center()

        text = (await self._ocr_card(image, bbox)).lower()
        kind = self._classifier.classify(text, signals, has_close=detected_close is not None)
        primary_point = self._find_primary(bbox) if kind == PopupKind.REWARD_CLAIM else None

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
        return state.signals.overlay_present and screen_is_unknown

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

        No X template bank ships with this package, so detection relies on the
        learned model when available. Returns ``None`` otherwise — the caller
        applies the geometric center of ``close_region`` as the tap fallback.
        """
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

    def _find_primary(self, bbox: Region) -> Point:
        """Best-effort CTA point for a reward card: lower-center of the card.

        Claim / Collect buttons sit near the bottom-center of reward modals.
        Geometric, resolution-independent, and only ever used for
        ``REWARD_CLAIM`` (never for purchases).
        """
        cx = bbox.x + bbox.w // 2
        cy = bbox.y + int(bbox.h * 0.85)
        return Point(cx, cy)

    async def _ocr_card(self, image: np.ndarray, bbox: Region) -> str:
        """OCR only the modal bbox crop. Never the full frame."""
        result = await self._ocr.ocr_region(image, bbox, region_id="popup_card")
        return result.text or ""
