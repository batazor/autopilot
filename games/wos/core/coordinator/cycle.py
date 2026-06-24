"""The unified tick — one coherent decision across every channel.

:func:`coordinator.coordinate` is the raw allocator and :func:`march.plan_march`
already closes the MARCH channel end-to-end. This module is the analogous
composition for the *development* channels (construction / research / hero / pet):
it takes each domain planner's output, folds in **every** situational factor the
brain knows about, and produces one :class:`CyclePlan` the executor runs top to
bottom — so the bot acts as a single agent instead of N planners plus a pile of
unwired bias helpers.

The factors, in the order they apply (each is a pure helper that already exists):

1. **Schedule** — :func:`events.calendar_bias` lifts the domains a live points
   event rewards (and emits hoard/spend ``holds``).
2. **Quests** — :func:`dailies.daily_bias` lifts domains with an open daily task
   (harder near reset) and surfaces one-shot ``nudges`` + ``claims``.
   The two boost maps are combined with :func:`dailies.merge_boosts` (max per
   domain) and threaded into the planner→candidate adapters.
3. **Economy** — :func:`economy.economy_bias` turns a short resource into a
   per-producer construction lift (coal short → boost ``coal_mine``).
4. **Safety** — :func:`safety.assess_safety` gates: in danger it drops the
   troop-exposing domains and returns immediate defensive actions to run first.
5. **Feedback** — :func:`feedback.tuning` penalises actions stuck N ticks in a row
   so the bot stops banging on a wall.

Then :func:`coordinator.coordinate` fills the idle ``channels`` with the best
affordable, biased candidates. Pure composition, no IO: the caller runs the
domain planners + loads the graphs + reads the factor inputs (event windows,
daily tasks, threat, feedback history), and dispatches the resulting commits +
directives. Every factor input is optional — absent → that factor contributes
nothing, so the tick degrades gracefully while its live reader is still deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .adapters import (
    from_build_slate,
    from_charms_plan,
    from_hero_plan,
    from_pet_plan,
    from_research_plan,
    from_training_plan,
)
from .allocate import coordinate_optimal
from .coordinator import coordinate
from .dailies import DailyBias, daily_bias, merge_boosts
from .economy import economy_bias
from .events import CalendarBias, calendar_bias
from .feedback import FeedbackBias, apply_feedback, tuning
from .safety import SafetyDirective, apply_safety, assess_safety

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from games.wos.core.building.planner import BuildGraph, BuildSlate
    from games.wos.core.charms.planner import CharmPlan
    from games.wos.core.pets.planner import PetPlan
    from games.wos.core.research.planner import ResearchGraph, ResearchPlan
    from games.wos.core.roles import RoleProfile
    from games.wos.heroes.heroes.planner import HeroPlan
    from games.wos.troops.planner import TrainingPlan

    from .dailies import DailyTask
    from .economy import EconomyBias
    from .events import EventWindow
    from .feedback import FeedbackState
    from .model import CandidateAction, Channel, CoordinatorDecision
    from .safety import ThreatState


@dataclass(frozen=True, slots=True)
class CyclePlan:
    """The whole tick: what to run on each channel, plus the directives + trace.

    The executor's running order: :attr:`safety` actions first (urgency-sorted),
    then :attr:`daily` claims, then dispatch :attr:`decision` commits, then
    :attr:`daily` nudges, respecting :attr:`calendar` holds for speedup spending.
    The remaining fields (``candidates`` / ``boosts`` / ``economy`` / ``feedback``)
    are the trace that explains *why* — for the dashboard and tests.
    """

    decision: CoordinatorDecision               # channel commits to dispatch
    candidates: tuple[CandidateAction, ...]      # the final biased pool (trace)
    boosts: Mapping[str, float]                  # merged calendar + daily domain boosts
    calendar: CalendarBias                       # holds + needs_read + active_categories
    daily: DailyBias                             # nudges + claims the executor runs
    economy: EconomyBias                         # short / overflow resources + producer lift
    safety: SafetyDirective                      # defensive actions (run first) + safe_mode
    feedback: FeedbackBias                       # stuck keys backed off (trace)


def plan_cycle(
    *,
    channels: Sequence[Channel],
    balances: Mapping[str, int],
    build_slate: BuildSlate | None = None,
    build_graph: BuildGraph | None = None,
    research_plan: ResearchPlan | None = None,
    research_graph: ResearchGraph | None = None,
    hero_plan: HeroPlan | None = None,
    pet_plan: PetPlan | None = None,
    training_plan: TrainingPlan | None = None,
    charms_plan: CharmPlan | None = None,
    role: RoleProfile | None = None,
    event_windows: Sequence[EventWindow] = (),
    daily_tasks: Sequence[DailyTask] = (),
    seconds_to_reset: float | None = None,
    threat: ThreatState | None = None,
    feedback_state: FeedbackState | None = None,
    item_to_resource: Mapping[str, str] | None = None,
    bottleneck: Sequence[str] = (),
    caps: Mapping[str, int] | None = None,
    min_buffer: Mapping[str, int] | None = None,
    extra_candidates: Sequence[CandidateAction] = (),
    optimize: bool = True,
) -> CyclePlan:
    """Arbitrate the idle ``channels`` across every domain planner's output.

    Runs the bias pipeline (schedule → quests → economy → safety → feedback) over
    the candidates derived from ``build_slate`` / ``research_plan`` / ``hero_plan``
    / ``pet_plan`` (plus any ``extra_candidates`` — e.g. MARCH intel/gather, to run
    one truly unified coordinate pass), then fills the channels by priority within
    the shared ``balances``. See the module docstring for the factor order.
    """
    # 1-2. Schedule + quest boosts, combined (max per domain) for the adapters.
    calendar = calendar_bias(event_windows)
    daily = daily_bias(daily_tasks, seconds_to_reset=seconds_to_reset)
    boosts = merge_boosts(calendar.domain_boost, daily.domain_boost)

    # 3. Economy: a short resource lifts its producer's construction candidate.
    economy = economy_bias(
        balances, bottleneck=bottleneck, caps=caps, min_buffer=min_buffer, role=role
    )

    candidates: list[CandidateAction] = []
    if build_slate is not None and build_graph is not None:
        candidates.extend(from_build_slate(
            build_slate, build_graph,
            role=role,
            item_to_resource=item_to_resource,
            boosts=boosts,
            instance_boosts=economy.producer_boost,
        ))
    if research_plan is not None and research_graph is not None:
        candidates.extend(from_research_plan(research_plan, research_graph, role=role, boosts=boosts))
    if hero_plan is not None:
        candidates.extend(from_hero_plan(hero_plan, boosts=boosts))
    if pet_plan is not None:
        candidates.extend(from_pet_plan(pet_plan, boosts=boosts))
    if training_plan is not None:
        candidates.extend(from_training_plan(training_plan, role=role, boosts=boosts))
    if charms_plan is not None:
        candidates.extend(from_charms_plan(charms_plan, boosts=boosts))
    candidates.extend(extra_candidates)

    # 4. Safety gates (drops troop-exposing domains in danger) before allocation.
    directive = assess_safety(threat) if threat is not None else SafetyDirective(safe_mode=False)
    candidates = apply_safety(candidates, directive)

    # 5. Feedback penalises actions stuck N ticks in a row (self-healing).
    fb_bias = tuning(feedback_state) if feedback_state is not None else FeedbackBias()
    candidates = apply_feedback(candidates, fb_bias)

    solver = coordinate_optimal if optimize else coordinate
    decision = solver(channels, candidates, balances)
    return CyclePlan(
        decision=decision,
        candidates=tuple(candidates),
        boosts=boosts,
        calendar=calendar,
        daily=daily,
        economy=economy,
        safety=directive,
        feedback=fb_bias,
    )
