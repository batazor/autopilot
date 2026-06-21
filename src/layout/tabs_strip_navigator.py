"""Decision logic for navigating a segmented tab strip.

The segmenter (:mod:`layout.tabs_strip_segmenter`) reports what a tab strip
currently looks like. This module decides what the bot should do next:

* If a non-active tab still carries a red dot → click that tab (its scenario
  will fire on arrival).
* Else if the *active* tab still carries a red dot → hold: its page scenario
  hasn't cleared the badge yet, so the caller must not page away from unclaimed
  rewards. Yield and let the higher-priority page scenario claim first.
* Otherwise → tell the caller to move on (e.g. click ``next_page``); the
  caller knows whether more pages exist.

The active tab itself is intentionally not a click target here: when the bot
lands on a page, the page-specific scenario is responsible for clearing the
active tab's notification before the navigator is consulted again. If the
active tab still has a red dot, that means the page scenario hasn't run yet,
not that the navigator should re-click the page we're already on — but it also
means we must *not* advance past it (the ``hold`` action above), or the bot
pages away from rewards it never claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from layout.tabs_strip_segmenter import TabDetection


StripActionKind = Literal["click_tab", "advance_page", "hold", "done"]


@dataclass(frozen=True)
class StripAction:
    """One next-step decision over a tab strip.

    ``kind == "click_tab"`` → click ``tab_index`` (always a non-active tab).
    ``kind == "hold"`` → the active tab still carries a red dot; its page
    scenario hasn't cleared it yet. Do nothing this tick (don't advance) so the
    page scenario can claim first; the navigator is re-consulted next tick.
    ``kind == "advance_page"`` → no work on visible tabs *and* the active tab is
    clear; caller should click ``next_page`` (or whatever advances to the next
    strip view).
    ``kind == "done"`` → nothing left for the navigator; only fires when the
    strip is empty (segmenter returned no tabs).
    """

    kind: StripActionKind
    tab_index: int | None = None


def pick_next_strip_action(tabs: Sequence[TabDetection]) -> StripAction:
    """Pick the next click on a segmented strip.

    The first non-active tab (left-to-right) that still has a red dot wins.
    If no such tab exists but the *active* tab still carries a red dot, the
    current page's scenario hasn't claimed yet → ``hold`` (do not advance) so
    the bot stays put and lets that scenario run rather than paging away from
    unclaimed rewards. Only when nothing on the strip is dotted does the caller
    advance to the next page. An empty ``tabs`` list short-circuits to ``done``
    so callers can distinguish "segmenter found nothing" from "all tabs
    handled, time to advance".
    """
    if not tabs:
        return StripAction(kind="done")
    for t in tabs:
        if t.active:
            continue
        if t.has_red_dot:
            return StripAction(kind="click_tab", tab_index=t.index)
    # No inactive tab needs work. If the active tab still has a red dot, its
    # page scenario (higher priority than this navigator) hasn't cleared the
    # badge — hold instead of advancing so we don't page off unclaimed rewards.
    if any(t.active and t.has_red_dot for t in tabs):
        return StripAction(kind="hold")
    return StripAction(kind="advance_page")
