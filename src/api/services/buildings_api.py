"""Buildings reference payload for the Next.js /buildings page.

Single source of truth is the per-game YAML registry under
``games/<game>/db/buildings/`` (loaded by ``config.buildings``). The page
renders straight from this — no data is duplicated in the frontend.

Two dependency views are emitted:
- per-building ``requires`` — explicit unlock gates from the YAML (derived once
  by ``scripts/derive_building_requires.py``).
- per-level ``requires`` inside each ``requirements_by_level`` entry — resolved
  here from that level's free-text prerequisites, so the frontend can draw a
  node per (building, level) without re-parsing strings.
"""
from __future__ import annotations

import re
from typing import Any

from config.building_deps import name_index, refs_in_text
from config.buildings import get_building_registry


def get_buildings_payload() -> dict[str, Any]:
    registry = get_building_registry()
    names = name_index(registry.buildings)
    ids = set(registry.all_ids())
    # base building -> its Fire Crystal ladder twin ("Furnace FC1" refs).
    fc_twins = {
        bid.removeprefix("fire_crystal_"): bid
        for bid in ids
        if bid.startswith("fire_crystal_")
    }
    # Snap "FC <n>" ref levels to the twin's actual level labels ("FC 1" vs
    # "FC1-1" — the wiki mixes formats; whole-FC rows are spelled "FC <n>").
    label_by_norm = {
        b.id: {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in b.requirements_by_level}
        for b in registry.buildings
    }

    def snap_level(bid: str, level: int | str) -> int | str:
        if isinstance(level, int):
            return level
        norm = re.sub(r"[^a-z0-9]", "", str(level).lower())
        return label_by_norm.get(bid, {}).get(norm, level)

    buildings: list[dict[str, Any]] = []

    for b in registry.buildings:
        # Wiki level labels sort numerics first ("1".."30"), then the Fire
        # Crystal ladder: "30-x" sub-levels, then "FC n" with sub-steps
        # ("FC 3-1" / "FC 5.1" — the wiki mixes separators).
        def label_key(label: str) -> tuple[int, float, float]:
            if label.isdigit():
                return (0, float(label), 0.0)
            nums = [float(x) for x in re.findall(r"\d+", label)]
            if label.lower().startswith("fc"):
                return (2, nums[0] if nums else 0.0, nums[1] if len(nums) > 1 else 0.0)
            return (1, nums[0] if nums else 0.0, nums[1] if len(nums) > 1 else 0.0)

        levels: dict[str, Any] = {}
        for level in sorted(b.requirements_by_level, key=label_key):
            raw = b.requirements_by_level[level]
            text = str(raw.get("prerequisites") or "").strip()
            level_requires = [
                {"building": dep, "level": snap_level(dep, lvl)}
                for dep, lvl in refs_in_text(text, names, fc_twins).items()
                if dep != b.id
            ]
            levels[level] = {
                "prerequisites": text,
                "construction_time": raw.get("construction_time"),
                "building_power": raw.get("building_power"),
                "build_cost": raw.get("build_cost") or [],
                "requires": level_requires,
            }
        numeric = [int(k) for k in b.requirements_by_level if k.isdigit()]
        max_level = max(numeric) if numeric else None
        buildings.append(
            {
                "id": b.id,
                "name": b.name,
                "category": b.category,
                "max_level": max_level,
                "requires": [
                    {"building": r.building, "level": r.level} for r in b.requires
                ],
                "requirements_by_level": levels,
            }
        )

    return {
        "game": "wos",
        "hub_id": "furnace",
        "buildings": buildings,
    }


def get_build_plan_payload(
    *,
    player: str | None = None,
    goal: str = "furnace",
    cap: float = 30.0,
    queues: int = 2,
    max_steps: int = 1000,
) -> dict[str, Any]:
    """Build schedule for the Next.js /buildings Gantt.

    With ``queues <= 1`` this is the single-queue furnace-first critical path
    (``planner.project_schedule``). With ``queues >= 2`` it's the parallel
    multi-queue sim (``planner.project_multi_schedule``): one queue rides the
    furnace chain while the rest fill with economy/camps. ``player`` starts the
    unroll from that player's recorded building levels; otherwise from scratch.
    Resources/speedups are not modelled — this is the build *order* and its raw
    game-time cost.
    """
    from games.wos.core.building.planner import (
        load_graph,
        project_multi_schedule,
        project_schedule,
    )

    levels: dict[str, Any] = {}
    start_from = "scratch"
    if player:
        from api.services import players as players_svc

        try:
            progress = players_svc.get_tree_progress(player)
            levels = dict(progress.get("buildings") or {})
            start_from = f"player:{player}"
        except KeyError:
            levels, start_from = {}, "scratch"

    queues = max(1, int(queues))
    graph = load_graph()
    if queues <= 1:
        sched = project_schedule(graph, levels, goal_id=goal, goal_cap=cap, max_steps=max_steps)
    else:
        sched = project_multi_schedule(
            graph, levels, queues=queues, goal_id=goal, goal_cap=cap, max_steps=max_steps
        )

    # First-touch order of buildings → the by-building Gantt grouping.
    order: list[dict[str, str]] = []
    seen: set[str] = set()
    steps: list[dict[str, Any]] = []
    for s in sched.steps:
        if s.building_id not in seen:
            seen.add(s.building_id)
            order.append({"id": s.building_id, "name": s.building_name})
        steps.append(
            {
                "seq": s.seq,
                "queue": s.queue,
                "track": s.track,
                "building_id": s.building_id,
                "instance_id": s.instance_id or s.building_id,
                "building_name": s.building_name,
                "from_level": s.from_level,
                "to_level": s.to_level,
                "to_rank": s.to_rank,
                "duration_s": s.duration_s,
                "start_s": s.start_s,
                "end_s": s.end_s,
                "power": s.power,
            }
        )

    return {
        "game": "wos",
        "goal": goal,
        "goal_cap": cap,
        "queues": sched.queues,
        "start_from": start_from,
        "reason": sched.reason,
        "truncated": sched.truncated,
        "total_time_s": sched.total_time_s,
        "step_count": len(steps),
        "buildings": order,
        "steps": steps,
    }
