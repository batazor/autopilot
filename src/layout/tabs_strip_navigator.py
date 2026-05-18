"""Decision logic for navigating a segmented tab strip.

The segmenter (:mod:`layout.tabs_strip_segmenter`) reports what a tab strip
currently looks like. This module decides what the bot should do next:

* If a non-active tab still carries a red dot → click that tab (its scenario
  will fire on arrival).
* Otherwise → tell the caller to move on (e.g. click ``next_page``); the
  caller knows whether more pages exist.

The active tab itself is intentionally not a click target here: when the bot
lands on a page, the page-specific scenario is responsible for clearing the
active tab's notification before the navigator is consulted again. If the
active tab still has a red dot, that means the page scenario hasn't run yet,
not that the navigator should re-click the page we're already on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from layout.tabs_strip_segmenter import TabDetection


StripActionKind = Literal["click_tab", "advance_page", "done"]


@dataclass(frozen=True)
class StripAction:
    """One next-step decision over a tab strip.

    ``kind == "click_tab"`` → click ``tab_index`` (always a non-active tab).
    ``kind == "advance_page"`` → no work on visible tabs; caller should click
    ``next_page`` (or whatever advances to the next strip view).
    ``kind == "done"`` → nothing left for the navigator; only fires when the
    strip is empty (segmenter returned no tabs).
    """

    kind: StripActionKind
    tab_index: int | None = None


def pick_next_strip_action(tabs: Sequence[TabDetection]) -> StripAction:
    """Pick the next click on a segmented strip.

    The first non-active tab (left-to-right) that still has a red dot wins.
    If no such tab exists, the strip itself has no more work to drive — the
    caller advances to the next page. An empty ``tabs`` list short-circuits
    to ``done`` so callers can distinguish "segmenter found nothing" from
    "all tabs handled, time to advance".
    """
    if not tabs:
        return StripAction(kind="done")
    for t in tabs:
        if t.active:
            continue
        if t.has_red_dot:
            return StripAction(kind="click_tab", tab_index=t.index)
    return StripAction(kind="advance_page")
