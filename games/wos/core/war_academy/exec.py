"""War Academy exec handlers — value-greedy WA-tech planner + reader/dispatcher.

Sibling of ``games/wos/core/research_center/exec.py``. The War Academy is a
separate building from the Research Center; it researches the **T11 Helios /
T12 Exalted troop techs** (``db/research.yaml`` branches ``t11_*`` / ``t12_*``,
each level carrying ``gate: "FCx"`` → ``war_academy_fc``). Those nodes are gated
on the War Academy's own FC level (a building independent of the RC), which the
research planner already models via :func:`plan_next`'s ``war_academy_fc`` param.

Three handlers, mirroring the research_center spine:

* ``plan_next_war_academy`` — PURE COMPUTE. Reads ``research.levels.*`` +
  ``war_academy.fc`` from the instance hash, runs the value-greedy planner over a
  **War-Academy-scoped subgraph** (only the ``t11_*`` / ``t12_*`` branches), and
  stashes the pick in ``planner.next_war_academy`` (+ name / branch / to-level /
  reason). No device IO; fully unit-tested.

* ``sync_war_academy_levels`` — the on-device reader. Navigates to the War Academy,
  opens its tech tree, OCR-sweeps each tab's (tile name, level), and mirrors
  ``research.levels.<id>`` (shared with the RC reader — same node ids, one graph)
  + ``war_academy.fc``.

* ``start_planned_war_academy`` — the dispatcher. Opens the WA tech tree, switches
  to the planned tech's tab, scroll-locates its tile, and taps the in-tree
  **Research** button.

The tile OCR / pill / locate-and-tap mechanics are the SAME overlay engine as the
Research Center tech tree, so the two device handlers reuse research_center's
proven helpers. **On-device geometry (WA tab layout) is UNVERIFIED**: the War
Academy is "Not yet built" on every available dev account (bs1 furnace 30, bs3
furnace 1), so the tab centres below are an estimate carried over from the RC
tree and the ``start_idle_war_academy`` scenario ships **disabled** until they can
be calibrated against a real War Academy capture (mirror research_center's
``references/`` + ``area.yaml`` workflow, then flip the scenario on).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# Reuse the Research Center tech-tree helpers — the War Academy uses the same
# overlay engine (tile columns, name-row OCR, level pill, locate-and-tap), so the
# device mechanics are identical; only the building we navigate to, the branch→tab
# mapping and the node scope differ.
from games.wos.core.research_center.exec import (
    _ensure_on_tree,
    _infer_maxed_predecessors,
    _locate_and_tap_tile,
    _ocr_text,
    _read_research_levels,
    _research_levels_from_ocr_rows,
    _state_get_int,
    _state_get_str,
    _sweep_research_tiles,
)

from layout.types import Point

if TYPE_CHECKING:
    from games.wos.core.research.planner import ResearchGraph

logger = logging.getLogger(__name__)

# War Academy branches in db/research.yaml (the T11 Helios / T12 Exalted troop
# techs). Everything outside these lives in the Research Center tree.
_WA_BRANCH_PREFIXES: tuple[str, ...] = ("t11_", "t12_")


def is_war_academy_branch(branch: str) -> bool:
    return str(branch or "").startswith(_WA_BRANCH_PREFIXES)


def war_academy_subgraph(graph: ResearchGraph) -> ResearchGraph:
    """A :class:`ResearchGraph` containing only the War Academy branches.

    Built with the same :func:`build_graph` the loader uses, so the tier ladder
    and reverse-children index are rederived over just the WA nodes. The WA techs'
    cross-line ``requires`` stay within the ``t11_*`` / ``t12_*`` branches, so the
    subgraph is prereq-self-contained (a dropped non-WA prereq would otherwise read
    as permanently unsatisfied)."""
    from games.wos.core.research.planner.model import build_graph

    nodes = [n for n in graph.nodes.values() if is_war_academy_branch(n.branch)]
    order = [b for b in graph.branch_order if is_war_academy_branch(b)]
    labels = {b: graph.branch_labels[b] for b in order if b in graph.branch_labels}
    return build_graph(nodes, order, labels)


def branch_to_wa_tab(branch: str) -> str:
    """Map a WA branch id to the War Academy tech-tree tab key.

    The War Academy tree is organised by troop type (Infantry / Marksman / Lancer)
    with a T11/T12 tier toggle, so the tab key is the troop type; the tier comes
    from the branch's ``t11_`` / ``t12_`` prefix (consumed by the dispatcher's
    tier toggle). UNVERIFIED tab geometry — see the module docstring."""
    b = str(branch or "").strip().lower()
    for troop in ("infantry", "marksman", "lancer"):
        if b.endswith(troop):
            return troop
    return "infantry"


def war_academy_tier(branch: str) -> str:
    """``"t11"`` / ``"t12"`` tier of a WA branch (the tree's tier toggle)."""
    b = str(branch or "").strip().lower()
    return "t12" if b.startswith("t12_") else "t11"


async def _exec_plan_next_war_academy(ctx: Any) -> None:
    """Pick the next War Academy tech → ``planner.next_war_academy``.

    Reads ``research.levels.*`` (populated by ``sync_war_academy_levels`` /
    ``sync_research_levels`` — shared node ids) and ``war_academy.fc`` from the
    instance hash, runs the value-greedy planner over the WA-scoped subgraph with
    ``war_academy_fc`` gating, and stores the pick. Pure compute — recommendation
    is only as complete as the level coverage (``wa_gated`` until the WA FC level
    is read on-device)."""
    from games.wos.core.research.planner import load_research_graph, plan_next

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await r.hgetall(inst_key)
    except Exception:
        state = {}
    levels = _read_research_levels(state)
    wa_fc = _state_get_int(state, "war_academy.fc", 0)

    wa_graph = war_academy_subgraph(load_research_graph())
    # rc_level is unused for WA nodes (all carry war_academy_fc>0 → gated on wa_fc);
    # pass 0. ``war_academy_fc`` activates the WA gate fold in the planner.
    plan = plan_next(wa_graph, levels, 0, war_academy_fc=wa_fc)
    step = plan.step
    mapping: dict[str, str] = {"planner.war_academy_reason": plan.reason}
    if step is not None:
        mapping["planner.next_war_academy"] = step.node_id
        mapping["planner.next_war_academy_name"] = step.name
        mapping["planner.next_war_academy_branch"] = step.branch
        mapping["planner.next_war_academy_to_level"] = str(step.to_level)
    else:
        mapping["planner.next_war_academy"] = ""
    try:
        await r.hset(inst_key, mapping=mapping)
    except Exception:
        logger.debug("plan_next_war_academy: state write failed", exc_info=True)

    nxt = step.node_id if step is not None else None
    ctx.result.update(
        {"action": "planned", "next": nxt, "reason": plan.reason,
         "war_academy_fc": wa_fc, "techs": len(levels)}
    )
    logger.info(
        "plan_next_war_academy: next=%s reason=%s wa_fc=%d techs=%d instance=%s",
        nxt, plan.reason, wa_fc, len(levels), ctx.instance_id,
    )


async def _exec_sync_war_academy_levels(ctx: Any) -> None:
    """Read War Academy tech levels on-device → mirror ``research.levels.*``.

    Reuses research_center's tab sweep (same overlay engine) but scoped to the WA
    subgraph, so only ``t11_*`` / ``t12_*`` tiles bind. Writes the player + instance
    hashes (research levels are per-account). NOTE: ``_sweep_research_tiles`` is
    tuned for the RC tree's Growth/Economy/Battle tabs — the WA tab layout is
    UNVERIFIED, so this runs only behind the disabled scenario until calibrated."""
    from games.wos.core.research.planner import load_research_graph

    from tasks.dsl_exec.context import _resolve_player_id_for_device_level_exec

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return

    wa_graph = war_academy_subgraph(load_research_graph())
    rows, wa_fc = await _sweep_research_tiles(ctx, wa_graph)
    if not rows and wa_fc <= 0:
        ctx.result.update({"reason": "no_tiles_read"})
        return

    levels = _infer_maxed_predecessors(_research_levels_from_ocr_rows(rows, wa_graph), wa_graph)
    mapping: dict[str, str] = {f"research.levels.{nid}": str(lvl) for nid, lvl in levels.items()}
    if wa_fc > 0:
        mapping["war_academy.fc"] = str(wa_fc)
    if not mapping:
        ctx.result.update({"reason": "no_tiles_recognized"})
        return

    player_id = await _resolve_player_id_for_device_level_exec(ctx)
    keys = [f"wos:instance:{ctx.instance_id}:state"]
    if player_id:
        keys.append(f"wos:player:{player_id}:state")
    try:
        for key in keys:
            await r.hset(key, mapping=mapping)
    except Exception:
        logger.debug("sync_war_academy_levels: state write failed", exc_info=True)
        ctx.result.update({"reason": "state_persist_failed"})
        return

    ctx.result.update({"action": "stored", "levels": levels, "war_academy_fc": wa_fc,
                       "player_id": player_id})
    logger.info(
        "sync_war_academy_levels: %d techs read wa_fc=%d player=%s instance=%s",
        len(levels), wa_fc, player_id or "-", ctx.instance_id,
    )


# Tech-detail "Research" button label region (same engine as the RC tree).
_RESEARCH_BTN_BBOX = (53.0, 75.0, 39.0, 4.7)
_RESEARCH_BTN_XY = (72.5, 77.4)


async def _exec_start_planned_war_academy(ctx: Any) -> None:
    """Open the WA tech tree → switch to the planned tech's tab → find + start it.

    Reads ``planner.next_war_academy`` (set by ``plan_next_war_academy``), opens the
    War Academy tech tree and locates + taps the planned tile, then taps the in-tree
    **Research** button (never the gem-spending *Finish*). Reuses research_center's
    ``_locate_and_tap_tile`` (engine-generic). The WA tab switch geometry is
    UNVERIFIED, so this runs only behind the disabled scenario."""
    from games.wos.core.research.planner import load_research_graph

    from tasks import dsl_runtime

    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return
    inst_key = f"wos:instance:{ctx.instance_id}:state"
    try:
        state = await r.hgetall(inst_key)
    except Exception:
        state = {}
    target_id = _state_get_str(state, "planner.next_war_academy")
    target_name = _state_get_str(state, "planner.next_war_academy_name")
    branch = _state_get_str(state, "planner.next_war_academy_branch")
    if not target_id:
        ctx.result.update({"reason": "no_plan"})
        return

    wa_graph = war_academy_subgraph(load_research_graph())
    actions = dsl_runtime.bot_actions()
    oc = dsl_runtime.ocr_client()
    iid = ctx.instance_id

    from games.wos.core.research_center.exec import _capture

    frame = await _capture(actions, iid)
    if frame is None:
        ctx.result.update({"reason": "capture_failed"})
        return
    frame, _fc = await _ensure_on_tree(actions, oc, iid, frame)
    if frame is None:
        ctx.result.update({"reason": "tree_not_opened"})
        return

    # NOTE: WA tab switching (troop type + T11/T12 tier toggle) is uncalibrated —
    # see module docstring. branch_to_wa_tab/war_academy_tier resolve the intent;
    # wiring them to taps needs a real WA capture. We still attempt the locate so
    # the dispatcher is exercisable once the tree is open on the right tab.
    tab = branch_to_wa_tab(branch)
    tier = war_academy_tier(branch)
    ctx.result.update({"wa_tab": tab, "wa_tier": tier})

    found = await _locate_and_tap_tile(actions, oc, iid, wa_graph, target_id)
    if not found:
        ctx.result.update({"reason": "tile_not_found", "next": target_id,
                           "tab": tab, "tier": tier})
        return

    frame = await _capture(actions, iid)
    btn = ""
    if frame is not None:
        dh, dw = frame.shape[:2]
        btn = await _ocr_text(oc, frame, _RESEARCH_BTN_BBOX, dw, dh,
                              preprocess="word_line", region_id="wa_research_btn")
    if "research" not in btn.lower():
        try:
            actions.system_back(iid)
        except Exception:
            logger.debug("start_planned_war_academy: system_back failed", exc_info=True)
        ctx.result.update({"reason": "not_researchable", "next": target_id,
                           "name": target_name, "btn_ocr": btn})
        return

    x_pct, y_pct = _RESEARCH_BTN_XY
    h, w = frame.shape[:2]
    actions.tap(iid, Point(int(x_pct / 100 * w), int(y_pct / 100 * h)))
    ctx.result.update({"action": "started", "next": target_id, "name": target_name,
                       "tab": tab, "tier": tier})
    logger.info("start_planned_war_academy: started=%s tab=%s instance=%s",
                target_id, tab, iid)


DSL_EXEC_HANDLERS = {
    "plan_next_war_academy": _exec_plan_next_war_academy,
    "sync_war_academy_levels": _exec_sync_war_academy_levels,
    "start_planned_war_academy": _exec_start_planned_war_academy,
}
