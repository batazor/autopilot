"""Pure broadcaster election — exactly one account per alliance posts.

Many accounts can sit in one alliance, but the chat should see a reminder once.
The runner elects a single broadcaster: the **lowest fid** among the alliance's
accounts that are *eligible* (the ``planner.broadcast_eligible`` opt-out is off)
and *currently active* on a device (so it actually ticks). Deterministic, so all
ticking accounts agree on the same winner without coordinating.

The cooldown + claim locks in :mod:`~.keys` are the hard anti-duplication guard;
this election is the deterministic primary gate layered on top.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class Candidate:
    """One account that could broadcast for its alliance / state."""

    fid: str
    alliance: str
    eligible: bool = True
    state: str = ""        # game state/server number — world chat is per-state


def _fid_sort_key(fid: str) -> tuple[int, int, str]:
    """Sort numeric fids ascending and before any non-numeric ones (stable)."""
    s = str(fid).strip()
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def elect_broadcaster(
    candidates: Iterable[Candidate],
    alliance: str,
    active_fids: Iterable[str],
) -> str | None:
    """The fid that should broadcast for ``alliance``, or ``None`` if no one can.

    ``active_fids`` is the set of accounts currently active on some device. A
    candidate qualifies when it is eligible, in this alliance, and active.
    """
    al = str(alliance or "").strip()
    if not al:
        return None
    active = {str(f).strip() for f in active_fids if str(f).strip()}
    pool = [
        c
        for c in candidates
        if c.eligible
        and str(c.fid).strip()
        and str(c.fid).strip() in active
        and str(c.alliance or "").strip() == al
    ]
    if not pool:
        return None
    return min(pool, key=lambda c: _fid_sort_key(c.fid)).fid


def elect_world_broadcaster(
    candidates: Iterable[Candidate],
    state: str,
    active_fids: Iterable[str],
) -> str | None:
    """The single fid that should post a **world** message for ``state``, or ``None``.

    World chat is per game-state, so one account per state posts (lowest active
    eligible fid on that state) — alliance membership is ignored. When ``state`` is
    empty (not read yet), the filter is skipped so a post can still happen.
    """
    active = {str(f).strip() for f in active_fids if str(f).strip()}
    st = str(state or "").strip()
    pool = [
        c
        for c in candidates
        if c.eligible
        and str(c.fid).strip()
        and str(c.fid).strip() in active
        and (not st or str(c.state or "").strip() == st)
    ]
    if not pool:
        return None
    return min(pool, key=lambda c: _fid_sort_key(c.fid)).fid
