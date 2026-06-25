"""Structural + wiring checks for the research_center node and planner spine."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

from navigation import screen_graph
from services import bind_active_game
from tasks.dsl_exec.context import DslExecContext

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_exec_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "research_center_exec_test", MODULE_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- structure ---------------------------------------------------------------
def test_module_manifest_declares_research_center() -> None:
    meta = _load_yaml("module.yaml")
    assert meta["id"] == "research_center"


def test_planner_cron_reader_and_dispatch_all_enabled() -> None:
    """The full loop is live: the pure-compute planner cron, the on-device tech-
    level reader, and the start-idle dispatcher all ship enabled now that the
    tech-tree is labeled and the sweep/dispatch execs are implemented."""
    cron = _load_yaml("scenarios/by_cron/research.plan_tick.cron.yaml")
    assert cron["enabled"] is True
    assert cron["steps"][0]["exec"] == "plan_next_research"

    reader = _load_yaml("scenarios/sync_research_levels.yaml")
    dispatch = _load_yaml("scenarios/start_idle_research.yaml")
    assert reader["enabled"] is True
    assert dispatch["enabled"] is True


def test_start_idle_research_calls_dispatcher_exec() -> None:
    """The dispatcher scenario wires plan → navigate → start_planned_research."""
    dispatch = _load_yaml("scenarios/start_idle_research.yaml")
    execs = _collect_execs(dispatch.get("steps") or [])
    assert "plan_next_research" in execs
    assert "navigate_to_building" in execs
    assert "start_planned_research" in execs


def _collect_execs(steps: list) -> set[str]:
    out: set[str] = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("exec"), str):
            out.add(step["exec"])
        out |= _collect_execs(step.get("steps") or [])
    return out


def test_area_doc_declares_all_labeled_regions() -> None:
    """area.yaml carries every region the reader/dispatcher rely on."""
    doc = _load_yaml("area.yaml")
    names = {
        r.get("name")
        for screen in (doc.get("screens") or [])
        for r in (screen.get("regions") or [])
    }
    for need in (
        "research_center.title",
        "research_center.tab.growth",
        "research_center.tab.economy",
        "research_center.tab.battle",
        "research_center.tile.name",
        "research_center.tile.level",
        "research_center.center.level",
        "research_center.button.research",
    ):
        assert need in names, f"missing region {need}"


def test_branch_to_tab_maps_troop_branches_to_battle() -> None:
    mod = _load_exec_module()
    assert mod.branch_to_tab("growth") == "growth"
    assert mod.branch_to_tab("economy") == "economy"
    assert mod.branch_to_tab("battle") == "battle"
    assert mod.branch_to_tab("t11_infantry") == "battle"
    assert mod.branch_to_tab("t12_lancer") == "battle"


def test_screen_graph_exposes_research_center_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_screen_verify_config()
    rules = screen_graph.screen_verify_rules("research_center")
    assert rules, "research_center must be a verifiable FSM node"
    assert any(r.get("ocr") == "research_center.title" for r in rules)


# --- tile-name → node-id matcher (pure) --------------------------------------
def test_match_tile_to_node_resolves_known_tech() -> None:
    from games.wos.core.research.planner import load_research_graph

    mod = _load_exec_module()
    graph = load_research_graph()
    assert mod._match_tile_to_node("Bandaging I", graph) == "bandaging_i"
    # OCR garble (lowercase l for I) still resolves
    assert mod._match_tile_to_node("Camp Expansion l", graph) == "camp_expansion_i"
    # unrecognised tile → None (skipped, never mis-bound)
    assert mod._match_tile_to_node("zzz not a tech", graph) is None


def test_match_tile_to_node_tier_disambiguation() -> None:
    """The matcher must keep tiers apart (names differ only by roman suffix) and
    not let a bare tier token subset-match everything — the bug that made the
    dispatcher miss frontier tiles."""
    from games.wos.core.research.planner import load_research_graph

    mod = _load_exec_module()
    graph = load_research_graph()
    # Heavy OCR garble of a frontier tile still resolves to the right tier.
    assert mod._match_tile_to_node("siting IV", graph) == "skirmishing_iv"
    assert mod._match_tile_to_node("Picket ines IV", graph) == "picket_lines_iv"
    # A clean sibling tier resolves to ITS tier, not a neighbour.
    assert mod._match_tile_to_node("Skirmishing VI", graph) == "skirmishing_vi"
    assert mod._match_tile_to_node("Skirmishing V", graph) == "skirmishing_v"
    # A bare tier token must NOT subset-match any "* IV" tile.
    assert mod._match_tile_to_node("iv", graph) is None


def test_infer_maxed_predecessors_backfills_lower_tiers() -> None:
    """A read tile implies its same-line lower tiers are maxed (the tier ladder)."""
    from games.wos.core.research.planner import load_research_graph

    mod = _load_exec_module()
    graph = load_research_graph()
    # Only tier III was read; I and II must be backfilled to their max levels.
    got = mod._infer_maxed_predecessors({"bandaging_iii": 4}, graph)
    assert got["bandaging_iii"] == 4
    assert got["bandaging_i"] == graph.spec("bandaging_i").max_level
    assert got["bandaging_ii"] == graph.spec("bandaging_ii").max_level
    # An existing higher record is never lowered.
    got2 = mod._infer_maxed_predecessors({"bandaging_ii": 1, "bandaging_iii": 4}, graph)
    assert got2["bandaging_ii"] == graph.spec("bandaging_ii").max_level


def test_research_levels_from_ocr_rows_maps_and_drops_unknown() -> None:
    from games.wos.core.research.planner import load_research_graph

    mod = _load_exec_module()
    graph = load_research_graph()
    rows = [("Bandaging I", 2), ("Camp Expansion I", 1), ("garbage xyz", 5)]
    got = mod._research_levels_from_ocr_rows(rows, graph)
    assert got == {"bandaging_i": 2, "camp_expansion_i": 1}


# --- planner wiring (state → plan_next → state) ------------------------------
@pytest.mark.asyncio
async def test_exec_plan_next_research_writes_pick(redis_async: Any) -> None:
    """Seed research levels + RC level in the instance hash → the handler runs the
    value-greedy planner and stashes its pick in planner.next_research."""
    bind_active_game("wos")
    mod = _load_exec_module()

    inst_key = "wos:instance:bs1:state"
    await redis_async.hset(
        inst_key,
        mapping={
            "research.center.level": "30",
            "research.levels.bandaging_i": "0",
        },
    )

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["plan_next_research"](ctx)

    assert ctx.result["action"] == "planned"
    assert ctx.result["reason"] == "selected"
    assert ctx.result["next"]  # a tech was picked
    next_research = await redis_async.hget(inst_key, "planner.next_research")
    next_name = await redis_async.hget(inst_key, "planner.next_research_name")
    reason = await redis_async.hget(inst_key, "planner.research_reason")

    def _dec(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    assert _dec(next_research) == ctx.result["next"]
    assert _dec(next_name)  # display name written
    assert _dec(reason) == "selected"


@pytest.mark.asyncio
async def test_exec_sync_research_levels_noops_without_device(
    redis_async: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no bot runtime/device, the sweep returns nothing and the handler
    no-ops cleanly instead of raising or writing garbage."""
    from tasks import dsl_runtime

    def _no_runtime() -> Any:
        msg = "no device in unit test"
        raise RuntimeError(msg)

    monkeypatch.setattr(dsl_runtime, "bot_actions", _no_runtime)
    mod = _load_exec_module()
    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["sync_research_levels"](ctx)
    assert ctx.result["reason"] == "no_tiles_read"


@pytest.mark.asyncio
async def test_exec_start_planned_research_noops_without_plan(redis_async: Any) -> None:
    """The dispatcher no-ops (no device taps) when no tech has been planned."""
    mod = _load_exec_module()
    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["start_planned_research"](ctx)
    assert ctx.result["reason"] == "no_plan"
