"""Broadcaster-election unit tests (pure)."""
from __future__ import annotations

from modules.broadcast.election import (
    Candidate,
    elect_broadcaster,
    elect_world_broadcaster,
)

_ABC = "ABC"


def _roster() -> list[Candidate]:
    return [
        Candidate(fid="100", alliance=_ABC, eligible=True, state="1"),
        Candidate(fid="9", alliance=_ABC, eligible=True, state="1"),
        Candidate(fid="50", alliance=_ABC, eligible=False, state="1"),  # opted out
        Candidate(fid="7", alliance="XYZ", eligible=True, state="2"),   # other alliance/state
    ]


def test_elects_lowest_active_eligible_fid() -> None:
    # 9 and 100 are eligible+ABC; both active → lowest fid 9 wins (numeric, not lexical).
    assert elect_broadcaster(_roster(), _ABC, {"9", "100"}) == "9"


def test_ineligible_excluded() -> None:
    # Only 50 (ineligible) and 100 active; 50 is opted out → 100.
    assert elect_broadcaster(_roster(), _ABC, {"50", "100"}) == "100"


def test_inactive_excluded() -> None:
    # 9 is eligible but not active; 100 active → 100.
    assert elect_broadcaster(_roster(), _ABC, {"100"}) == "100"


def test_other_alliance_excluded() -> None:
    # 7 (XYZ) is active but not in ABC → no one for ABC.
    assert elect_broadcaster(_roster(), _ABC, {"7"}) is None


def test_none_when_no_active() -> None:
    assert elect_broadcaster(_roster(), _ABC, set()) is None


def test_empty_alliance_returns_none() -> None:
    assert elect_broadcaster(_roster(), "", {"9"}) is None


def test_numeric_ordering_beats_lexical() -> None:
    roster = [Candidate(fid="10", alliance=_ABC), Candidate(fid="2", alliance=_ABC)]
    # Lexically "10" < "2"; numerically 2 < 10 → 2 must win.
    assert elect_broadcaster(roster, _ABC, {"10", "2"}) == "2"


def test_world_election_is_per_state() -> None:
    # World chat: lowest active eligible fid ON THAT STATE wins (alliance ignored).
    # State 1 has 9, 100 (eligible) → 9 wins; 7 is on state 2 so it's excluded.
    assert elect_world_broadcaster(_roster(), "1", {"9", "7", "100"}) == "9"
    # State 2 → only 7 qualifies.
    assert elect_world_broadcaster(_roster(), "2", {"9", "7", "100"}) == "7"
    # Ineligible (50) skipped even if active.
    assert elect_world_broadcaster(_roster(), "1", {"50", "100"}) == "100"
    # No active → None.
    assert elect_world_broadcaster(_roster(), "1", set()) is None


def test_world_election_unknown_state_falls_back_to_global() -> None:
    # Empty state (not read yet) → don't filter by state, pick lowest overall.
    assert elect_world_broadcaster(_roster(), "", {"9", "7", "100"}) == "7"
