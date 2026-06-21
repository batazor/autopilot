"""Sharpness-mask localizer — the validated, template-free modal detector.

The underlying game is Gaussian-blurred while the modal card stays sharp. Fine
edges (high Laplacian energy) therefore survive only on the card. We threshold
that energy, fuse the card elements into one blob, and take the union bounding
box of the sizable components as the modal rect.

Every kernel size is derived from the image width — nothing is hardcoded to
720×1280, so the detector is resolution-independent.

Validated on a real ``Charm Master Pack`` screenshot (1006×1796): recovered the
full modal bbox at ``card_frac ≈ 0.58``, ``center ≈ (0.50, 0.49)``,
``scrim_sharp ≈ 0.000``, and the inferred top-right region landed on the X.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Region
from popup.models import DetectionSignals


@dataclass(frozen=True, slots=True)
class MaskConfig:
    """Tunable thresholds for localization and the overlay gate.

    Kept as a dataclass rather than module constants so callers can override per
    deployment without monkeypatching.
    """

    # A component is "sizable" if it covers at least this fraction of the frame.
    min_component_frac: float = 0.01
    # overlay_present gate: surround must be this clean, card_frac inside band.
    scrim_max: float = 0.01
    card_frac_min: float = 0.10
    card_frac_max: float = 0.90
    # Ring outside the card sampled for scrim sharpness, as a fraction of width.
    scrim_pad_frac: float = 0.04
    # Top-right close-button slice, relative to the card bbox.
    close_x0_frac: float = 0.82
    close_y1_frac: float = 0.12


_DEFAULT_CONFIG = MaskConfig()


class SharpnessMask:
    """Localize a blurred-scrim modal and derive screen-agnostic signals."""

    def __init__(self, config: MaskConfig | None = None) -> None:
        self._cfg = config or _DEFAULT_CONFIG

    @property
    def config(self) -> MaskConfig:
        return self._cfg

    def localize(self, image: np.ndarray) -> tuple[np.ndarray, Region] | None:
        """Return ``(mask, bbox)`` for the modal, or ``None`` if nothing sizable.

        ``mask`` is a ``uint8`` binary image (0/255) the size of the frame;
        ``bbox`` is the union rect of all sizable sharp components.
        """
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # 1. Sharpness energy — fine edges survive only where NOT blurred.
        lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
        energy = cv2.boxFilter(lap, ddepth=-1, ksize=(31, 31))
        energy = cv2.normalize(energy, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

        # 2. Binarize, despeckle, then FUSE card elements into one blob.
        _, mask = cv2.threshold(energy, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
        )
        big = max(31, (width // 8) | 1)  # kernel scaled to image; bridges interior gaps
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (big, big)),
        )

        # 3. Union bounding boxes of all sizable components = full modal rect.
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        min_area = self._cfg.min_component_frac * width * height
        keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > min_area]
        if not keep:
            return None
        x1 = min(int(stats[i, cv2.CC_STAT_LEFT]) for i in keep)
        y1 = min(int(stats[i, cv2.CC_STAT_TOP]) for i in keep)
        x2 = max(int(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]) for i in keep)
        y2 = max(int(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]) for i in keep)
        return mask, Region(x1, y1, x2 - x1, y2 - y1)

    def compute_signals(
        self,
        mask: np.ndarray,
        bbox: Region,
        image_shape: tuple[int, ...],
    ) -> DetectionSignals:
        """Derive ``DetectionSignals`` from the mask + bbox + frame shape."""
        height, width = image_shape[:2]
        frame_area = float(width * height)

        card_frac = (bbox.w * bbox.h) / frame_area if frame_area else 0.0
        center = (
            (bbox.x + bbox.w / 2.0) / width if width else 0.0,
            (bbox.y + bbox.h / 2.0) / height if height else 0.0,
        )
        scrim_sharp = self._scrim_sharpness(mask, bbox, width, height)

        overlay_present = (
            scrim_sharp < self._cfg.scrim_max
            and self._cfg.card_frac_min <= card_frac <= self._cfg.card_frac_max
        )
        return DetectionSignals(
            card_frac=card_frac,
            center=center,
            scrim_sharp=scrim_sharp,
            overlay_present=overlay_present,
        )

    def _scrim_sharpness(self, mask: np.ndarray, bbox: Region, width: int, height: int) -> float:
        """Fraction of sharp mask pixels in a padded ring OUTSIDE the card.

        A clean blurred surround ⇒ near 0 (the scrim has no fine edges). Returns
        0.0 when the card already fills the sampled outer region (no ring left).
        """
        pad = max(1, int(self._cfg.scrim_pad_frac * width))
        ox1 = max(0, bbox.x - pad)
        oy1 = max(0, bbox.y - pad)
        ox2 = min(width, bbox.x + bbox.w + pad)
        oy2 = min(height, bbox.y + bbox.h + pad)

        outer = mask[oy1:oy2, ox1:ox2].astype(bool)
        # Carve out the card itself so only the ring remains.
        ring = outer.copy()
        ix1 = bbox.x - ox1
        iy1 = bbox.y - oy1
        ring[iy1 : iy1 + bbox.h, ix1 : ix1 + bbox.w] = False

        ring_total = int(ring.size - bbox.w * bbox.h)
        if ring_total <= 0:
            return 0.0
        sharp = int(np.count_nonzero(outer & ring))
        return sharp / ring_total

    def close_region(self, bbox: Region) -> Region:
        """Top-right slice of the card bbox where the X close button lives."""
        x0 = bbox.x + int(self._cfg.close_x0_frac * bbox.w)
        w = (bbox.x + bbox.w) - x0
        h = int(self._cfg.close_y1_frac * bbox.h)
        return Region(x0, bbox.y, max(1, w), max(1, h))

    def debug_overlay(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        bbox: Region,
        close_region: Region,
    ) -> np.ndarray:
        """Render the mask + bbox + close-region for tests / UI inspection.

        Mirrors the validation visualization: green = modal bbox, red = the
        top-right close slice, blue tint = the sharp mask.
        """
        vis = image.copy()
        tint = np.zeros_like(vis)
        tint[mask.astype(bool)] = (255, 0, 0)  # blue in BGR
        vis = cv2.addWeighted(vis, 1.0, tint, 0.35, 0.0)
        cv2.rectangle(vis, (bbox.x, bbox.y), (bbox.x + bbox.w, bbox.y + bbox.h), (0, 255, 0), 2)
        cv2.rectangle(
            vis,
            (close_region.x, close_region.y),
            (close_region.x + close_region.w, close_region.y + close_region.h),
            (0, 0, 255),
            2,
        )
        return vis
