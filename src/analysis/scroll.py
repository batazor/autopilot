"""End-of-scroll detection by page fingerprint repetition.

Reusable helper for the DSL ``while_scroll`` step. The pattern is borrowed from
Insomniac's ``ScrollEndDetector``: after each swipe, fingerprint the visible
list region; if the last K fingerprints are identical we've stopped making
progress and stop the loop. Works without a dedicated "end of list" UI marker.

Fingerprinting uses :func:`layout.template_match._phash64` on the BGR crop, so
small anti-aliasing differences don't spuriously break the repeat detection.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from layout.template_match import _phash64

if TYPE_CHECKING:
    import numpy as np


class ScrollEndDetector:
    """Track recent page fingerprints; signal end after ``repeats_to_end`` matches.

    Hamming distance under ``tolerance`` is treated as a match — small glow /
    blinking selection highlights on the same list don't reset the counter.
    """

    def __init__(self, repeats_to_end: int = 3, tolerance: int = 2) -> None:
        self._target = max(1, int(repeats_to_end))
        self._tolerance = max(0, int(tolerance))
        self._fingerprints: deque[int] = deque(maxlen=self._target)

    def push(self, fingerprint: int) -> None:
        self._fingerprints.append(int(fingerprint))

    def is_the_end(self) -> bool:
        if len(self._fingerprints) < self._target:
            return False
        last = self._fingerprints[-1]
        return all(
            _hamming64(other, last) <= self._tolerance for other in self._fingerprints
        )

    @property
    def fingerprints(self) -> list[int]:
        return list(self._fingerprints)


def fingerprint_region_bgr(patch_bgr: np.ndarray) -> int:
    """Stable 64-bit perceptual hash for a region crop."""
    return _phash64(patch_bgr)


def _hamming64(a: int, b: int) -> int:
    return int(a ^ b).bit_count()
