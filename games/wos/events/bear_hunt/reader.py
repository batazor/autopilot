"""Read both traps' cooldown + enhancement level off the Bear Hunt info page.

The two traps share one info page but their state lives behind separate tabs, so
we tap each tab (:data:`~.parser.TRAP_TAPS`), let it settle, capture once, and
crop two fixed bands: the cooldown timer (:data:`~.parser.COOLDOWN_BBOX`) and the
``Lv. N`` enhancement level (:data:`~.parser.LEVEL_BBOX`). One capture feeds both
reads. A trap with no cooldown line parses to ``None`` (ready now); a missing
level parses to ``None``.

``actions`` (``capture_screen_bgr`` / ``tap``) and ``ocr`` are injected like the
calendar reader so the loop is swappable and the parsers stay unit-testable.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from games.wos.events.bear_hunt.parser import (
    COOLDOWN_BBOX,
    COOLDOWN_PREPROCESS,
    LEVEL_BBOX,
    LEVEL_PREPROCESS,
    TRAP_TAPS,
    parse_cooldown,
    parse_level,
)

if TYPE_CHECKING:
    from datetime import timedelta

logger = logging.getLogger(__name__)

TAB_SETTLE_MS = 700  # tab switch + redraw


async def read_trap_info(
    actions: Any,
    instance_id: str,
    ocr: Any,
    *,
    tab_settle_ms: int = TAB_SETTLE_MS,
) -> dict[str, tuple[timedelta | None, int | None]]:
    """Tap each trap tab and return ``{trap_id: (remaining_cooldown, level)}``."""
    from layout.types import Point

    out: dict[str, tuple[timedelta | None, int | None]] = {}
    cx0, cy0, cx1, cy1 = COOLDOWN_BBOX
    lx0, ly0, lx1, ly1 = LEVEL_BBOX
    for trap_id, (tx, ty) in TRAP_TAPS.items():
        await asyncio.to_thread(
            actions.tap, instance_id, Point(tx, ty), approval_region="bear_hunt.trap"
        )
        await asyncio.sleep(tab_settle_ms / 1000.0)
        frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        if frame is None:
            out[trap_id] = (None, None)
            continue
        cd_text, _ = ocr(frame[cy0:cy1, cx0:cx1], preprocess=COOLDOWN_PREPROCESS)
        lv_text, _ = ocr(frame[ly0:ly1, lx0:lx1], preprocess=LEVEL_PREPROCESS)
        out[trap_id] = (parse_cooldown(cd_text), parse_level(lv_text))
    logger.info("bear_hunt trap info: instance=%s %s", instance_id, out)
    return out
