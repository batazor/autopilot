"""Calendar/event awareness — bias the coordinator toward what scores points now.

Reading the calendar isn't enough to be smart; you have to *play* the schedule.
Two levers (confirmed by the meta — e.g. the Power Up event: power from
construction / research / training all become event points, and the calendar warns
you ~2 days out so you hoard speedups for the window):

1. **Boost now** — while a points event that rewards activity X is live, lift the
   coordinator priority of the domains that produce X, so effort routes there.
2. **Hold for later** — when such an event is live or imminent, emit a "hoard
   speedups / save the big spend for the window" signal so accelerants land where
   they score points instead of being burned early.

Pure: consumes parsed :class:`EventWindow`s (the calendar reader already writes
``event_<slug>`` flags + start/end windows to player state) and returns a
:class:`CalendarBias`. The slug→category catalog is config-as-code below; for
phased events (themed days/hours) an optional ``phase_category`` from a future
in-event phase reader refines the boost, else the event's default categories apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- Reward categories → coordinator domains they boost -----------------------
CONSTRUCTION = "construction"
RESEARCH = "research"
TRAINING = "training"
GATHER = "gather"
COMBAT = "combat"
ANY_POWER = "any_power"          # power from construction/research/training all count

CATEGORY_DOMAINS: dict[str, tuple[str, ...]] = {
    CONSTRUCTION: ("building_progression", "building_economy", "building_camp"),
    RESEARCH: ("research",),
    TRAINING: ("troops", "building_camp"),
    GATHER: ("gather", "building_economy"),
    COMBAT: ("raids",),
    ANY_POWER: ("building_progression", "building_economy", "research", "troops"),
}


@dataclass(frozen=True, slots=True)
class EventSpec:
    """How a known event rewards activity (catalog entry).

    The catalog is only a *prior* — the precise reward/theme is dynamic and can be
    read accurately only once the event is live. ``phased`` events (themed days /
    hours, e.g. Hall of Chiefs, Arms Race) need that live read to know today's
    theme; until then their boost is provisional. Non-phased events (Power Up — any
    power counts) have nothing to read, so the catalog is already authoritative.
    """

    categories: tuple[str, ...]      # what it rewards (the prior / fallback)
    points_event: bool = True        # worth aligning activity to
    hoard_speedups: bool = True      # save accelerants for its window
    phased: bool = False             # reward varies by day/hour → read it live


# Slugs match the calendar's ``event_<slug>`` flags / events/ module dirs. Best
# effort — extend as events are classified. Non-points events (bear_hunt, etc.) are
# omitted: they're gates handled by `active_when`, not activity boosts.
EVENT_CATALOG: dict[str, EventSpec] = {
    "power_up": EventSpec((ANY_POWER,)),                 # any power counts → not phased
    "hall_of_chief": EventSpec((ANY_POWER,), phased=True),
    "armament_competition": EventSpec((CONSTRUCTION, RESEARCH, TRAINING), phased=True),
    "state_of_power": EventSpec((CONSTRUCTION, RESEARCH, TRAINING, COMBAT), phased=True),
    "svs": EventSpec((CONSTRUCTION, RESEARCH, TRAINING, COMBAT), phased=True),
    "tundra_arms_league": EventSpec((TRAINING, COMBAT), phased=True),
    "alliance_championship": EventSpec((COMBAT,)),
    # Alliance Showdown: 6 themed stages, each rewards a different spend (Mithril, Wild
    # Marks, hero shards, …). The precise per-stage domain tilt is supplied directly to
    # plan_cycle via stage_domain_tilt (games/wos/core/alliance_showdown) since it lifts
    # investment domains the reward categories don't cover; this catalog entry is the
    # construction/research/training/combat FLOOR + slug recognition (holds, hoard).
    "alliance_showdown": EventSpec((CONSTRUCTION, RESEARCH, TRAINING, COMBAT), phased=True),
    # Icefire Warhymn League: fixed mission tracks (all live at once, no themed
    # days) reward gathering + hunting Polar Terrors for Warhymn Testament, so it
    # is non-phased — the reward mix is already known, nothing to read live. The
    # "use any speedup" track means accelerants score points, so hoard them for
    # the window. Alliance-contribution / login tracks map to no boostable domain.
    # Reference tables: games/wos/events/icefire_warhymn_league/data/.
    "icefire_warhymn_league": EventSpec((GATHER, COMBAT)),
}

DEFAULT_BOOST = 1.5            # confirmed: live read (or non-phased) → full lift
PROVISIONAL_BOOST = 1.2       # active phased event, theme not read yet → guess softly
DEFAULT_HOLD_HORIZON_S = 2 * 86_400   # Power Up is announced ~2 days out


@dataclass(frozen=True, slots=True)
class EventWindow:
    """A parsed calendar event for one player (from the reader)."""

    slug: str
    active: bool = False
    starts_in_s: float = 0.0          # >0 if upcoming
    ends_in_s: float = 0.0            # remaining while active
    phase_category: str | None = None  # live themed phase, if a phase reader knows it


@dataclass(frozen=True, slots=True)
class HoldSignal:
    """Advice to save speedups / a big discretionary spend for a points window."""

    slug: str
    reason: str
    until_s: float                    # seconds until the window opens (0 = open now)


@dataclass(frozen=True, slots=True)
class CalendarBias:
    """Coordinator inputs derived from the live event schedule."""

    domain_boost: Mapping[str, float] = field(default_factory=dict)
    holds: tuple[HoldSignal, ...] = ()
    active_categories: tuple[str, ...] = ()
    needs_read: tuple[str, ...] = ()     # active phased events whose theme to read live


def calendar_bias(
    windows: Sequence[EventWindow],
    *,
    boost_factor: float = DEFAULT_BOOST,
    provisional_boost: float = PROVISIONAL_BOOST,
    hold_horizon_s: float = DEFAULT_HOLD_HORIZON_S,
) -> CalendarBias:
    """Derive domain boosts + hoard-speedup holds from the live event schedule.

    Confidence model (the reward is dynamic — only read it once live):
    * upcoming event → ``holds`` only, no boost (we don't act on a guess);
    * active + theme read (``phase_category``) or non-phased → full ``boost_factor``;
    * active phased event, theme not read yet → ``provisional_boost`` on the catalog
      guess, plus the slug in ``needs_read`` so the bot reads it to confirm.
    """
    domain_boost: dict[str, float] = {}
    holds: list[HoldSignal] = []
    active_cats: set[str] = set()
    needs_read: list[str] = []

    for w in windows:
        spec = EVENT_CATALOG.get(w.slug)
        if spec is None or not spec.points_event:
            continue

        if w.active:
            if w.phase_category:                  # read live → authoritative theme
                cats, factor = (w.phase_category,), boost_factor
            elif spec.phased:                     # live but theme unread → soft guess
                cats, factor = spec.categories, provisional_boost
                needs_read.append(w.slug)
            else:                                 # nothing to read (e.g. any-power)
                cats, factor = spec.categories, boost_factor
            for cat in cats:
                active_cats.add(cat)
                for domain in CATEGORY_DOMAINS.get(cat, ()):
                    domain_boost[domain] = max(domain_boost.get(domain, 1.0), factor)
            if spec.hoard_speedups:
                holds.append(HoldSignal(w.slug, "spend now — points window open", 0.0))
        elif spec.hoard_speedups and 0 < w.starts_in_s <= hold_horizon_s:
            holds.append(HoldSignal(w.slug, "points event soon — hoard speedups", w.starts_in_s))

    return CalendarBias(
        domain_boost=domain_boost,
        holds=tuple(holds),
        active_categories=tuple(sorted(active_cats)),
        needs_read=tuple(needs_read),
    )
