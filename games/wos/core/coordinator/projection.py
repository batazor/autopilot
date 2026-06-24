"""Lookahead — project the unified tick forward in time into an ETA timeline.

The per-tick coordinator decides *what to do now*; this answers *when will things
happen*. It replays the domain planners on a shared clock, advancing state as each
channel finishes, so the construction queues and the research queue progress in
parallel — and crucially the **cross-dependency** falls out for free: research is
Research-Center-gated, the RC is a building, so a tech stays blocked until the
construction sim raises the RC enough to unlock it ("RC reaches Lv X at +9d → the
next tech tier opens then").

Event-driven and resource-blind (the build/research *order* and its raw game-time
cost, speedups + balances assumed available — same model as
:mod:`building.planner.schedule`). At each event the idle channels pull their next
planner pick; the clock then jumps to the next completion. Pure: graphs + current
levels in, a :class:`CycleProjection` (timeline + milestone ETAs) out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .objective import TRACK_DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.building.planner import BuildGraph
    from games.wos.core.research.planner import ResearchGraph
    from games.wos.core.roles import RoleProfile

# Mirror building.planner.schedule's bound on a from-scratch unroll.
DEFAULT_MAX_STEPS = 2000
_RESEARCH_CENTER = "research_center"
_WAR_ACADEMY = "war_academy"


@dataclass(frozen=True, slots=True)
class ProjectedTask:
    """One upgrade placed on the timeline (seconds are offsets from t=0)."""

    channel: str              # "construction" | "research"
    domain: str               # building_progression / building_economy / research / …
    seq: int                  # 1-based order the sim started tasks in
    key: str                  # instance/spec id (build) or node id (research)
    name: str
    from_level: str
    to_level: str
    duration_s: int
    start_s: int
    end_s: int
    queue: int = 0            # construction queue index (research always 0)
    power: int | None = None


@dataclass(frozen=True, slots=True)
class Milestone:
    """A highlighted ETA derived from the timeline."""

    kind: str                 # "build_goal" | "rc_level" | "goal_reached"
    label: str
    at_s: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CycleProjection:
    """The forward projection: full timeline + curated milestone ETAs."""

    timeline: tuple[ProjectedTask, ...]
    milestones: tuple[Milestone, ...]
    total_time_s: int
    reason: str               # goal_reached | horizon | blocked | truncated
    truncated: bool
    construction_queues: int
    construction_speed_pct: float = 0.0   # hero build-speed buff applied to build ETAs
    research_speed_pct: float = 0.0       # hero research-speed buff applied to research ETAs


def _label(value: Any) -> str:
    s = str(value if value is not None else 0).strip()
    return s or "0"


def _free_queue(busy_queues: set[int], n: int) -> int:
    for i in range(n):
        if i not in busy_queues:
            return i
    return 0


def project_cycle(
    *,
    build_graph: BuildGraph,
    build_levels: Mapping[str, Any],
    research_graph: ResearchGraph,
    research_levels: Mapping[str, int] | None = None,
    construction_queues: int = 2,
    role: RoleProfile | None = None,
    goal_id: str = "furnace",
    goal_cap: float = 30.0,
    horizon_s: float | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    construction_speed_pct: float = 0.0,
    research_speed_pct: float = 0.0,
) -> CycleProjection:
    """Project construction + research forward until the build goal (or a horizon).

    ``build_levels`` / ``research_levels`` map id → current level (missing = not
    built / level 0). The research queue's RC gate reads the *simulated*
    ``research_center`` level, so techs unlock as the build sim raises it. Stops at
    the goal, ``horizon_s`` (if set), a stall, or ``max_steps`` (``truncated``).
    ``construction_speed_pct`` / ``research_speed_pct`` shorten build / research
    durations respectively (hero speed buffs → earlier milestone ETAs).
    """
    from games.wos.core.building.planner.planner import (
        BLOCKED,
        GOAL_REACHED,
        current_rank,
        plan_builds,
    )
    from games.wos.core.building.planner.schedule import apply_speed
    from games.wos.core.research.planner import plan_next as research_plan_next

    build_state: dict[str, Any] = dict(build_levels)
    build_label: dict[str, str] = {k: _label(v) for k, v in build_state.items()}
    res_state: dict[str, int] = dict(research_levels or {})
    builds_ip: list[dict[str, Any]] = []      # in-progress builds {instance_id,to_level,end_s,queue}
    res_ip: list[dict[str, Any]] = []          # in-progress research {node_id,to_level,end_s}
    timeline: list[ProjectedTask] = []
    seq = 0
    reason = "incomplete"
    t = 0

    def complete_through(now: int) -> None:
        nonlocal builds_ip, res_ip
        for b in sorted((b for b in builds_ip if b["end_s"] <= now), key=lambda b: b["end_s"]):
            build_state[b["instance_id"]] = b["to_level"]
            build_label[b["instance_id"]] = b["to_level"]
        builds_ip = [b for b in builds_ip if b["end_s"] > now]
        for r in sorted((r for r in res_ip if r["end_s"] <= now), key=lambda r: r["end_s"]):
            res_state[r["node_id"]] = r["to_level"]
        res_ip = [r for r in res_ip if r["end_s"] > now]

    n = max(1, int(construction_queues))
    while len(timeline) < max_steps:
        if current_rank(build_state, goal_id) >= goal_cap:
            reason = GOAL_REACHED
            break
        complete_through(t)
        if current_rank(build_state, goal_id) >= goal_cap:
            reason = GOAL_REACHED
            break

        # Fill idle construction queues with the top ready candidates (one plot each).
        free_slots = n - len(builds_ip)
        if free_slots > 0:
            busy_ids = {b["instance_id"] for b in builds_ip}
            busy_queues = {b["queue"] for b in builds_ip}
            slate = plan_builds(
                build_graph, build_state, role=role,
                free_queues=n, goal_id=goal_id, goal_cap=goal_cap,
            )
            for cand in slate.candidates:
                if free_slots <= 0:
                    break
                if cand.instance_id in busy_ids:
                    continue
                spec = build_graph.spec(cand.spec_id)
                lvl = spec.level(cand.to_level) if spec else None
                dur = apply_speed(int(cand.time_s), construction_speed_pct)
                q = _free_queue(busy_queues, n)
                seq += 1
                timeline.append(ProjectedTask(
                    channel="construction",
                    domain=TRACK_DOMAIN.get(cand.track, "building_economy"),
                    seq=seq, key=cand.instance_id,
                    name=spec.name if spec else cand.spec_id,
                    from_level=build_label.get(cand.instance_id, "0"),
                    to_level=cand.to_level, duration_s=dur,
                    start_s=t, end_s=t + dur, queue=q,
                    power=lvl.power if lvl else None,
                ))
                builds_ip.append({"instance_id": cand.instance_id, "to_level": cand.to_level,
                                  "end_s": t + dur, "queue": q})
                busy_ids.add(cand.instance_id)
                busy_queues.add(q)
                free_slots -= 1

        # Fill the (single) research queue if idle — RC gate reads the live build state.
        if not res_ip:
            rc = int(current_rank(build_state, _RESEARCH_CENTER))
            # War Academy FC level (rank 30+x → FCx) gates the T11/T12 troop techs, so
            # they unlock only as the build sim raises the War Academy — not the RC.
            wa_fc = max(0, int(current_rank(build_state, _WAR_ACADEMY)) - 30)
            rplan = research_plan_next(research_graph, res_state, rc,
                                       war_academy_fc=wa_fc, role=role)
            step = rplan.step
            if step is not None:
                node = research_graph.spec(step.node_id)
                lvl = node.level_at(step.to_level) if node else None
                dur = apply_speed(int(lvl.time_s) if lvl else 0, research_speed_pct)
                seq += 1
                timeline.append(ProjectedTask(
                    channel="research", domain="research", seq=seq,
                    key=step.node_id, name=step.name,
                    from_level=str(step.from_level), to_level=str(step.to_level),
                    duration_s=dur, start_s=t, end_s=t + dur, queue=0,
                    power=lvl.power if lvl else None,
                ))
                res_ip.append({"node_id": step.node_id, "to_level": step.to_level, "end_s": t + dur})

        # Advance the clock to the next completion.
        ends = [b["end_s"] for b in builds_ip] + [r["end_s"] for r in res_ip]
        if not ends:
            reason = BLOCKED                      # nothing running and nothing startable
            break
        nxt = min(ends)
        if horizon_s is not None and nxt > horizon_s:
            reason = "horizon"
            break
        if nxt > t:
            t = nxt
        elif nxt < t:                             # guard against a bad duration parse
            reason = BLOCKED
            break
        # nxt == t → instant (0-duration) tasks were just scheduled; don't move the
        # clock — the next iteration's complete_through(t) applies them and re-plans.

    truncated = len(timeline) >= max_steps
    if truncated:
        reason = "truncated"
    total = max((tk.end_s for tk in timeline), default=0)
    milestones = _milestones(timeline, build_graph, goal_id, total if reason == GOAL_REACHED else None)
    return CycleProjection(
        timeline=tuple(timeline),
        milestones=milestones,
        total_time_s=total,
        reason=reason,
        truncated=truncated,
        construction_queues=n,
        construction_speed_pct=construction_speed_pct,
        research_speed_pct=research_speed_pct,
    )


def _milestones(
    timeline: list[ProjectedTask],
    build_graph: BuildGraph,
    goal_id: str,
    goal_at_s: int | None,
) -> tuple[Milestone, ...]:
    """Curate the highlight ETAs: each goal-building level, each RC level, the goal."""
    spec = build_graph.spec(goal_id)
    goal_name = spec.name if spec else goal_id
    out: list[Milestone] = []
    for tk in timeline:
        if tk.channel != "construction":
            continue
        if tk.key == goal_id:
            out.append(Milestone("build_goal", f"{goal_name} {tk.to_level}", tk.end_s))
        elif tk.key == _RESEARCH_CENTER:
            out.append(Milestone("rc_level", f"Research Center {tk.to_level}", tk.end_s,
                                  "unlocks higher research tiers"))
    if goal_at_s is not None:
        out.append(Milestone("goal_reached", f"{goal_name} goal reached", goal_at_s))
    out.sort(key=lambda m: (m.at_s, m.kind))
    return tuple(out)
