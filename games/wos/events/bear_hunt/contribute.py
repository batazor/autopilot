"""Contribute Explosive Arrowheads into the Trap Enhancement (raise bear level).

On the Bear Hunt info page the **Trap Enhancement** icon carries a red dot while
either trap can still be levelled. We decide which traps to feed by their
**level** read off the info page (``Lv. N`` next to the icon, per trap tab): a
trap at :data:`~.parser.MAX_LEVEL` is maxed and skipped; below it still has room.
The alliance progress bar (``X/300``) is irrelevant — only the level matters.

For each chosen tab we open the popup, tap **Contribute**, drag the amount slider
fully right (= all available arrows), confirm, and commit.

All on-screen geometry is fixed (static layout), so coordinates live here as
constants. ``actions`` is injected like the cooldown reader, keeping
:func:`is_maxed` / :func:`select_targets` pure and unit-tested.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from games.wos.events.bear_hunt.parser import MAX_LEVEL

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Info-page Trap Enhancement icon (bottom-right, carries the red dot).
ICON_TAP = (645, 1180)
# Trap Enhancement *popup* tab tap centres (distinct from the info-page tabs the
# level read uses).
POPUP_TAB_TAPS: dict[str, tuple[int, int]] = {"1": (210, 400), "2": (510, 400)}
# Popup "Contribute" pill (opens the amount window).
POPUP_CONTRIBUTE = (360, 940)
# Amount window: the commit button + slider drag endpoints. Dragging the handle
# fully right sets the amount to all available arrows.
QTY_CONTRIBUTE = (360, 830)
SLIDER_FROM = (200, 702)
SLIDER_TO = (458, 702)
# Popup close (X).
POPUP_CLOSE = (660, 318)

SETTLE_MS = 700


def is_maxed(level: int | None) -> bool:
    """True when a trap's enhancement level is at the cap (skip it).

    A missing level (``None``, unreadable) is NOT treated as maxed — the operator
    wants to skip only on a confirmed max level, so it stays eligible.
    """
    return level is not None and level >= MAX_LEVEL


def select_targets(maxed: dict[str, bool]) -> list[str]:
    """Which trap tabs to contribute into, given each tab's maxed flag.

    Rule (per the operator): pour into every non-maxed tab. If both are maxed,
    pour into any one anyway (the first), so a red-dot/cron run still spends
    arrows rather than no-op. Order follows ``maxed`` insertion (Trap 1, Trap 2).
    """
    non_maxed = [tab for tab, m in maxed.items() if not m]
    if non_maxed:
        return non_maxed
    return [next(iter(maxed))] if maxed else []


async def _tap(actions: Any, iid: str, point: tuple[int, int], region: str) -> None:
    from layout.types import Point

    await asyncio.to_thread(
        actions.tap, iid, Point(point[0], point[1]), approval_region=region
    )


async def _set_max(actions: Any, iid: str) -> None:
    """Drag the amount slider fully right → all available arrows."""
    from layout.types import Point

    await asyncio.to_thread(
        actions.swipe, iid, Point(*SLIDER_FROM), Point(*SLIDER_TO), 400
    )
    await asyncio.sleep(SETTLE_MS / 1000.0)


async def contribute_traps(
    actions: Any,
    instance_id: str,
    targets: Iterable[str],
    *,
    settle_ms: int = SETTLE_MS,
) -> dict[str, str]:
    """Open the popup and contribute **all available** arrows into each trap in
    ``targets`` (slider to max).

    ``targets`` is decided by the caller from per-trap levels (see
    :func:`select_targets`). Returns ``{trap_id: outcome}``; leaves the popup closed.
    """
    target_set = set(targets)
    await _tap(actions, instance_id, ICON_TAP, "bear_hunt.trap_enhancement")
    await asyncio.sleep(settle_ms / 1000.0)

    results: dict[str, str] = {}
    for trap_id, tab_xy in POPUP_TAB_TAPS.items():
        if trap_id not in target_set:
            results[trap_id] = "skip_maxed"
            continue
        await _tap(actions, instance_id, tab_xy, "bear_hunt.te.tab")
        await asyncio.sleep(settle_ms / 1000.0)
        await _tap(actions, instance_id, POPUP_CONTRIBUTE, "bear_hunt.te.contribute")
        await asyncio.sleep(settle_ms / 1000.0)
        await _set_max(actions, instance_id)
        await _tap(actions, instance_id, QTY_CONTRIBUTE, "bear_hunt.te.commit")
        await asyncio.sleep(settle_ms / 1000.0)
        results[trap_id] = "contributed_max"

    await _tap(actions, instance_id, POPUP_CLOSE, "bear_hunt.te.close")
    await asyncio.sleep(settle_ms / 1000.0)
    logger.info("bear_hunt trap enhancement: instance=%s %s", instance_id, results)
    return results
