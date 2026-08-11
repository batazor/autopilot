"""Lossless frame capture for OCR- and template-heavy exec handlers.

The default screenshot backend is scrcpy, and its H.264 stream is fine for
overlay ticks. It is not fine for small text or small markers, and three modules
learned that separately and each wrote the same fallback chain:

* ``research_center`` — tech-tree tile names and level pills drop below the
  fuzzy match threshold, which was calibrated on pristine adb frames;
* ``main_menu`` — short City-panel row titles stop matching their section;
* ``intel`` — on bs3 (2026-08-08) a stream frame template-matched **zero** board
  pins while a fresh PNG of the same board found nine, so the run bailed as a
  claim-only pass and stranded the device on the board. The stamina counter has
  the same problem: the stream reads «14» where a PNG reads «114».

adb ``screencap`` costs roughly 300 ms and coexists with scrcpy, which is a
bargain for a handler that runs every few hours and is worthless if it misreads.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


async def capture_lossless(actions: Any, instance_id: str, *, what: str) -> np.ndarray | None:
    """A BGR frame via adb ``screencap``, falling back to the configured backend.

    ``what`` names the caller in the log lines — without it a capture failure
    reads identically from every module and says nothing about which reader is
    about to return empty.

    Returns ``None`` only when both backends fail; callers must handle it, since
    the alternative is OCR against a null frame.
    """
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr_adb, instance_id)
    except Exception:
        logger.debug("%s: adb capture failed, trying default backend", what, exc_info=True)

    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        logger.exception("%s: screen capture failed instance=%s", what, instance_id)
        return None
