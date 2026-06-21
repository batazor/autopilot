"""Arena opponent filter: the paid "exclude your own alliance" option.

Arena is hero PvP — the bot attacks other players' defense teams. Running a
fleet of accounts (plus partner/alliance farms) it is easy to end up pointing
your own bots at your own side. This is the pure decision behind the
**exclude-own-alliance** option: given an opponent's alliance tag and the set of
tags you consider "yours", should this opponent be skipped?

**Resolution lives at the edge, not here.** This module stays pure: it takes
the already-resolved ``enabled`` flag and the tags — nothing about OCR or Redis.

**"Alliances" is plural on purpose.** An operator running many accounts may own
several alliance tags, so "own tags" is a *set*. Per-account state carries one
``alliance.name`` (``state_schema`` ``Alliance.name``); assembling the operator's
union of owned tags across the fleet is the consumer's job (deferred).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Per-account toggle key — the single source of truth shared by the account
# options registry, the settings API, the dashboard control, and the Arena
# scenario that consumes it. Mirrors the per-character ``planner.*`` settings
# pattern (e.g. planner.role). MUST stay a *2-level* ``planner.<name>`` key:
# the per-gamer state store's setter writes into the free-form ``planner`` dict
# but does NOT auto-create deeper nesting, so a 3-level key would silently
# no-op (see GamerStateStore._set_nested).
SETTING_KEY = "planner.arena_exclude_own_alliance"

_NON_TAG = re.compile(r"[^A-Za-z0-9]")


def normalize_tag(tag: str | None) -> str:
    """Bare, comparable alliance tag: drop brackets/punctuation/space, upper-case.

    ``"[RoSe]" -> "ROSE"``, ``" abc " -> "ABC"``, ``None``/``""`` -> ``""``.
    OCR of the in-game ``[TAG]`` prefix is noisy, so compare on this canonical
    form rather than raw strings.
    """
    if not tag:
        return ""
    return _NON_TAG.sub("", tag).upper()


# Leading bracketed alliance tag on an OCR'd opponent label: "[Rxt]resion" ->
# "Rxt". WoS tags are ~3 alphanumerics; allow 2-4 for OCR slack and accept ()
# too in case OCR misreads the square-bracket glyphs.
_TAG_PREFIX = re.compile(r"^\s*[\[(]\s*([A-Za-z0-9]{2,4})\s*[\])]")


def tag_from_display_name(display: str | None) -> str:
    """Pull the bracketed alliance tag out of an OCR'd ``[TAG]Nickname`` label.

    ``"[Rxt]resion" -> "RXT"``, ``"[ZPp]palangsae0" -> "ZPP"``. Returns ``""``
    when there is no leading ``[TAG]`` (an un-tagged or unreadable name) — the
    caller then treats the opponent as *not* own and fights normally. Only the
    bracketed prefix is taken, never the nickname, so a long ``[TAG]LongNick``
    can't be mistaken for a tag.
    """
    if not display:
        return ""
    m = _TAG_PREFIX.match(display)
    return normalize_tag(m.group(1)) if m else ""


def is_own_alliance(opponent_tag: str | None, own_tags: Iterable[str]) -> bool:
    """Does the opponent's tag match any alliance the operator owns?

    An empty/unreadable opponent tag is treated as *not* own — when in doubt we
    don't suppress a fight (points are only gained by attacking; a missed skip
    just means one extra fight, a wrong skip wastes a challenge).
    """
    tag = normalize_tag(opponent_tag)
    if not tag:
        return False
    return tag in {normalize_tag(t) for t in own_tags}


@dataclass(frozen=True, slots=True)
class TargetVerdict:
    """Whether to skip an Arena opponent, and why."""

    skip: bool
    reason: str


def should_skip_opponent(
    opponent: str | None,
    own_tags: Iterable[str],
    *,
    enabled: bool,
) -> TargetVerdict:
    """Decide whether to skip an Arena opponent from its OCR'd ``[TAG]Nickname``.

    ``opponent`` is the raw label read off the challenge list (the
    ``arena.opponent.N`` text regions); the alliance tag is extracted from it.
    ``enabled`` is the already-resolved per-account toggle (:data:`SETTING_KEY`)
    — resolve it at the edge and pass the result in. With the option off,
    nothing is ever skipped (base behaviour:
    fight the top opponent).
    """
    if not enabled:
        return TargetVerdict(False, "filter_disabled")
    if is_own_alliance(tag_from_display_name(opponent), own_tags):
        return TargetVerdict(True, "own_alliance")
    return TargetVerdict(False, "enemy")


@dataclass(frozen=True, slots=True)
class TargetingPlan:
    """What to do with the current challenge list this pass."""

    action: str               # "fight" | "refresh" | "stop"
    fight_index: int | None   # 0-based row to fight (only when action == "fight")
    skipped: tuple[int, ...]  # rows skipped as own-alliance (diagnostics)
    reason: str


def plan_targets(
    opponents: Sequence[str | None],
    own_tags: Iterable[str],
    *,
    enabled: bool,
    can_refresh: bool = True,
) -> TargetingPlan:
    """Pick the next Arena opponent to fight, or decide to refresh the list.

    ``opponents`` are the OCR'd ``[TAG]Nickname`` labels of the visible
    challenge rows, top first; blank/``None`` entries are empty/unread rows and
    are ignored.

    Policy (operator-confirmed):

    * **disabled** -> fight the top readable row (base behaviour, no filtering).
    * **enabled, skip-row** -> fight the first row whose tag isn't one of ours.
    * **enabled, all own** -> nothing is fightable, so *refresh* the list to
      reroll — unless ``can_refresh`` is ``False`` (free refreshes spent), then
      *stop* rather than attack your own side.
    * **no readable rows** -> stop (the caller's squad-screen match then fails
      and the fight loop ends).
    """
    own = {normalize_tag(t) for t in own_tags}
    own.discard("")
    readable = [(i, str(lbl)) for i, lbl in enumerate(opponents) if lbl and str(lbl).strip()]
    if not readable:
        return TargetingPlan("stop", None, (), "no_opponents")

    if not enabled:
        return TargetingPlan("fight", readable[0][0], (), "filter_disabled")

    skipped: list[int] = []
    for i, label in readable:
        tag = tag_from_display_name(label)
        if tag and tag in own:
            skipped.append(i)
            continue
        return TargetingPlan("fight", i, tuple(skipped), "enemy")

    # Every readable row is our own alliance — reroll if we still can.
    if can_refresh:
        return TargetingPlan("refresh", None, tuple(skipped), "all_own_refresh")
    return TargetingPlan("stop", None, tuple(skipped), "all_own_no_refresh")
