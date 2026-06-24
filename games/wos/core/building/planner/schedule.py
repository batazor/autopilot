"""Unroll the build planner into a time-ordered Gantt schedule.

Two projections, both pure (no IO) and resource-blind (no speedups, balances
assumed available — this is the build *order* and its raw game-time cost):

* :func:`project_schedule` — single queue, furnace-first. Replays
  :func:`planner.plan_next` (apply the chosen upgrade, ask again) to unroll the
  canonical furnace-first order, laid end-to-end on one construction queue. This
  is the critical-path spine: with one real queue the bot builds furnace-first
  and nothing else (progression outranks economy and never idles).

* :func:`project_multi_schedule` — N construction queues running in parallel, an
  event-driven sim over :func:`planner.plan_builds`. Whenever a queue frees, it
  takes the highest-value ready candidate not already building — so one queue
  rides the furnace-first critical chain while the others fill with economy /
  camps (exactly the value-greedy multi-queue policy). Stops when the furnace
  goal completes; in-flight economy is left as scheduled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .model import level_rank
from .planner import (
    BLOCKED,
    DEFAULT_GOAL,
    DEFAULT_GOAL_CAP,
    GOAL_REACHED,
    SELECTED,
    current_rank,
    plan_builds,
    plan_next,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import BuildGraph

# Generous cap on a from-scratch → Furnace 30 unroll (a few hundred building
# levels). Bounds runaway loops; ``BuildSchedule.truncated`` flags when it bites.
DEFAULT_MAX_STEPS = 1000

PROGRESSION = "progression"


@dataclass(frozen=True, slots=True)
class ScheduledBuild:
    """One upgrade placed on the timeline (seconds are offsets from t=0)."""

    seq: int                  # 1-based position in the build order
    building_id: str          # db spec id ("shelter")
    building_name: str
    from_level: str           # level label before this upgrade ("0" = not built)
    to_level: str             # level label after ("11" / "FC-8")
    to_rank: float
    duration_s: int
    start_s: int
    end_s: int
    power: int | None
    queue: int = 0            # which construction queue (0-based)
    instance_id: str = ""     # plot id ("shelter_3"); == building_id for single-plot
    track: str = PROGRESSION  # progression | economy | camp | bottleneck


@dataclass(frozen=True, slots=True)
class BuildSchedule:
    """The full unrolled timeline plus its terminal state."""

    steps: tuple[ScheduledBuild, ...]
    total_time_s: int
    reason: str               # terminal planner reason (goal_reached / blocked / …)
    truncated: bool           # hit max_steps before reaching the goal
    queues: int = 1           # construction queues modelled
    construction_speed_pct: float = 0.0   # build-speed buff applied to durations


def _label(value: Any) -> str:
    """Display label for a level value (``0`` / ``None`` → ``"0"``)."""
    s = str(value if value is not None else 0).strip()
    return s or "0"


def apply_speed(time_s: int, speed_pct: float) -> int:
    """Wall-clock build time under a speed buff: ``+X%`` → ``time / (1 + X/100)``.

    The standard WoS speed-buff maths (a Construction-Speed hero like Zinman, a
    research-speed hero, etc.). ``speed_pct<=0`` leaves the raw game-time untouched.
    """
    if speed_pct <= 0 or time_s <= 0:
        return int(time_s)
    return int(round(time_s / (1.0 + speed_pct / 100.0)))


def project_schedule(
    graph: BuildGraph,
    levels: Mapping[str, Any],
    *,
    goal_id: str = DEFAULT_GOAL,
    goal_cap: float = DEFAULT_GOAL_CAP,
    max_steps: int = DEFAULT_MAX_STEPS,
    construction_speed_pct: float = 0.0,
) -> BuildSchedule:
    """Replay :func:`plan_next` from ``levels`` until the goal, as a timeline.

    ``levels`` maps ``building_id`` → current level (int / "10" / "FC-3"); missing
    means not built (start from scratch with ``{}``). Each pass takes the planner's
    chosen upgrade, appends it end-to-end on a single queue, and advances that
    building so the next pass sees it built. Stops when the goal is reached/blocked
    or ``max_steps`` is hit (``truncated``). ``construction_speed_pct`` shortens
    every build duration (a Construction-Speed hero buff → earlier ETAs).
    """
    state: dict[str, Any] = dict(levels)
    cur_label: dict[str, str] = {k: _label(v) for k, v in levels.items()}
    steps: list[ScheduledBuild] = []
    elapsed = 0
    reason = ""

    while len(steps) < max_steps:
        plan = plan_next(graph, state, goal_id=goal_id, goal_cap=goal_cap)
        reason = plan.reason
        step = plan.step
        if step is None:
            break

        spec = graph.spec(step.building_id)
        lvl = spec.level(step.to_level) if spec else None
        dur = apply_speed(lvl.time_s if lvl else 0, construction_speed_pct)
        steps.append(
            ScheduledBuild(
                seq=len(steps) + 1,
                building_id=step.building_id,
                building_name=spec.name if spec else step.building_id,
                from_level=cur_label.get(step.building_id, _label(0)),
                to_level=step.to_level,
                to_rank=step.to_rank,
                duration_s=dur,
                start_s=elapsed,
                end_s=elapsed + dur,
                power=lvl.power if lvl else None,
                queue=0,
                instance_id=step.building_id,
                track=PROGRESSION,
            )
        )
        elapsed += dur

        # Advance the chosen building; bail if the pick doesn't move it forward
        # (guards against a non-monotonic level parse looping forever).
        if level_rank(step.to_level) <= level_rank(state.get(step.building_id, 0)):
            break
        state[step.building_id] = step.to_level
        cur_label[step.building_id] = step.to_level

    truncated = len(steps) >= max_steps and reason == SELECTED
    return BuildSchedule(tuple(steps), elapsed, reason, truncated, queues=1,
                         construction_speed_pct=construction_speed_pct)


def project_multi_schedule(
    graph: BuildGraph,
    levels: Mapping[str, Any],
    *,
    queues: int = 2,
    role: RoleProfile | None = None,
    goal_id: str = DEFAULT_GOAL,
    goal_cap: float = DEFAULT_GOAL_CAP,
    max_steps: int = DEFAULT_MAX_STEPS,
    construction_speed_pct: float = 0.0,
) -> BuildSchedule:
    """Simulate ``queues`` construction queues building in parallel toward the goal.

    Event-driven over :func:`plan_builds`: advance to the earliest-free queue,
    apply any builds that finished by then, then hand that queue the top-value
    candidate whose plot isn't already under construction. One queue naturally
    rides the furnace-first chain (weight 100) while the rest fill with economy /
    camps. A queue with nothing buildable idles until the next completion unlocks
    work. Stops when the furnace reaches ``goal_cap`` (in *completed* levels).
    ``construction_speed_pct`` shortens every build duration (a Construction-Speed
    hero buff → earlier ETAs).
    """
    queues = max(1, queues)
    state: dict[str, Any] = dict(levels)
    cur_label: dict[str, str] = {k: _label(v) for k, v in levels.items()}
    free_at = [0] * queues                       # when each queue next goes idle
    in_progress: list[dict[str, Any]] = []        # {instance_id, to_level, end_s}
    steps: list[ScheduledBuild] = []
    reason = SELECTED

    def complete_through(t: int) -> None:
        """Apply (to ``state``) every build that has finished by time ``t``."""
        nonlocal in_progress
        done = sorted((b for b in in_progress if b["end_s"] <= t), key=lambda b: b["end_s"])
        for b in done:
            state[b["instance_id"]] = b["to_level"]
            cur_label[b["instance_id"]] = b["to_level"]
        in_progress = [b for b in in_progress if b["end_s"] > t]

    while len(steps) < max_steps:
        if current_rank(state, goal_id) >= goal_cap:
            reason = GOAL_REACHED
            break
        qi = min(range(queues), key=lambda i: free_at[i])
        t = free_at[qi]
        complete_through(t)
        if current_rank(state, goal_id) >= goal_cap:
            reason = GOAL_REACHED
            break

        busy = {b["instance_id"] for b in in_progress}
        slate = plan_builds(
            graph, state, role=role, free_queues=queues, goal_id=goal_id, goal_cap=goal_cap
        )
        cand = next((c for c in slate.candidates if c.instance_id not in busy), None)
        if cand is None:
            # Nothing this queue can start now. Idle until the next build frees a
            # plot / unlocks a prereq; if nothing is running, we're done/blocked.
            if not in_progress:
                reason = GOAL_REACHED if slate.reason != SELECTED else BLOCKED
                break
            nxt = min(b["end_s"] for b in in_progress)
            if nxt <= t:                          # safety: no forward progress
                break
            free_at[qi] = nxt
            continue

        spec = graph.spec(cand.spec_id)
        lvl = spec.level(cand.to_level) if spec else None
        dur = apply_speed(cand.time_s, construction_speed_pct)
        steps.append(
            ScheduledBuild(
                seq=len(steps) + 1,
                building_id=cand.spec_id,
                building_name=spec.name if spec else cand.spec_id,
                from_level=cur_label.get(cand.instance_id, _label(0)),
                to_level=cand.to_level,
                to_rank=cand.to_rank,
                duration_s=dur,
                start_s=t,
                end_s=t + dur,
                power=lvl.power if lvl else None,
                queue=qi,
                instance_id=cand.instance_id,
                track=cand.track,
            )
        )
        in_progress.append({"instance_id": cand.instance_id, "to_level": cand.to_level, "end_s": t + dur})
        free_at[qi] = t + dur

    total = max((s.end_s for s in steps), default=0)
    truncated = len(steps) >= max_steps and reason == SELECTED
    return BuildSchedule(tuple(steps), total, reason, truncated, queues=queues,
                         construction_speed_pct=construction_speed_pct)
