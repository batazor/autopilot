"""Value object for the most-recent in-scenario overlay match.

Bundles the two always-paired fields the DSL task previously tracked as separate
mutable attributes (``_last_match_region`` + ``_last_match_row``). They were set,
cleared, and read together; folding them into one frozen object makes the "do we
have a fresh match for this region?" check a single ``is not None`` test and
removes the chance of the pair drifting out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MatchResult:
    """A region name and the overlay hit row most recently matched for it.

    Kept so a following ``click:`` / ``while_match`` on the same region taps the
    matched location (``row``'s ``tap_*_pct`` / ``template_*`` / ``top_left``)
    instead of the static bbox center.
    """

    region: str
    row: dict[str, Any]
