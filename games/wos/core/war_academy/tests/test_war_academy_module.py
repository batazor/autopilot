"""War Academy node + WA-tech planner/reader/dispatcher checks.

The device half (sweep/locate geometry) is reused from research_center and runs
only behind the disabled scenario (the WA building is unreachable on dev
accounts), so these tests cover the pure-compute spine: WA-subgraph scoping, the
branch→tab/tier mapping, the planner exec (state → plan_next → state), and the
device handlers' clean no-op paths.
"""
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
        "war_academy_exec_test", MODULE_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_execs(steps: list) -> set[str]:
    out: set[str] = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("exec"), str):
            out.add(step["exec"])
        out |= _collect_execs(step.get("steps") or [])
    return out


# --- structure ---------------------------------------------------------------
def test_module_manifest() -> None:
    assert _load_yaml("module.yaml")["id"] == "war_academy"


def test_dispatcher_and_reader_ship_disabled() -> None:
    """Both device scenarios ship disabled until the WA tech-tree is calibrated
    on a real capture (the building is 'Not yet built' on every dev account)."""
    dispatch = _load_yaml("scenarios/start_idle_war_academy.yaml")
    reader = _load_yaml("scenarios/sync_war_academy_levels.yaml")
    assert dispatch["enabled"] is False
    assert reader["enabled"] is False


def test_start_idle_war_academy_wires_the_full_flow() -> None:
    dispatch = _load_yaml("scenarios/start_idle_war_academy.yaml")
    execs = _collect_execs(dispatch.get("steps") or [])
    assert "plan_next_war_academy" in execs
    assert "navigate_to_building" in execs
    assert "start_planned_war_academy" in execs


def test_screen_graph_exposes_war_academy_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_screen_verify_config()
    rules = screen_graph.screen_verify_rules("war_academy")
    assert rules, "war_academy must be a verifiable FSM node"
    assert any(r.get("ocr") == "war_academy.title" for r in rules)


# --- WA subgraph scoping + tab mapping (pure) --------------------------------
def test_subgraph_scopes_to_t11_t12_only() -> None:
    from games.wos.core.research.planner import load_research_graph

    mod = _load_exec_module()
    full = load_research_graph()
    wa = mod.war_academy_subgraph(full)
    assert wa.nodes, "WA subgraph must not be empty"
    # Every node is a t11_*/t12_* branch, and every WA node carries a positive
    # war_academy_fc gate (so the planner treats them as WA-gated).
    for nid, n in wa.nodes.items():
        assert n.branch.startswith(("t11_", "t12_")), nid
        assert max((lv.war_academy_fc for lv in n.levels), default=0) > 0, nid
    # Research Center nodes are excluded.
    assert "bandaging_i" not in wa.nodes
    assert any(b.startswith("t11_") for b in wa.branch_order)
    assert "growth" not in wa.branch_order


def test_branch_to_wa_tab_and_tier() -> None:
    mod = _load_exec_module()
    assert mod.branch_to_wa_tab("t11_infantry") == "infantry"
    assert mod.branch_to_wa_tab("t12_marksman") == "marksman"
    assert mod.branch_to_wa_tab("t11_lancer") == "lancer"
    assert mod.war_academy_tier("t11_infantry") == "t11"
    assert mod.war_academy_tier("t12_lancer") == "t12"
    assert mod.is_war_academy_branch("t12_infantry") is True
    assert mod.is_war_academy_branch("growth") is False


# --- planner wiring (state → plan_next → state) ------------------------------
@pytest.mark.asyncio
async def test_plan_next_war_academy_gates_on_fc(redis_async: Any) -> None:
    """With a high War Academy FC the planner picks a WA tech; with FC 0 every WA
    node is gated → wa_gated and no pick is written."""
    bind_active_game("wos")
    mod = _load_exec_module()
    inst_key = "wos:instance:bs1:state"

    # High FC → a WA tech is researchable and gets picked.
    await redis_async.delete(inst_key)
    await redis_async.hset(inst_key, mapping={"war_academy.fc": "10"})
    ctx = DslExecContext(redis_client=redis_async, player_id="",
                         instance_id="bs1", args={}, result={})
    await mod.DSL_EXEC_HANDLERS["plan_next_war_academy"](ctx)
    assert ctx.result["action"] == "planned"
    assert ctx.result["next"], "a WA tech should be picked at FC10"
    picked = await redis_async.hget(inst_key, "planner.next_war_academy")
    branch = await redis_async.hget(inst_key, "planner.next_war_academy_branch")

    def _dec(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    assert _dec(picked) == ctx.result["next"]
    assert _dec(branch).startswith(("t11_", "t12_"))

    # FC 0 → everything gated.
    await redis_async.delete(inst_key)
    await redis_async.hset(inst_key, mapping={"war_academy.fc": "0"})
    ctx2 = DslExecContext(redis_client=redis_async, player_id="",
                          instance_id="bs1", args={}, result={})
    await mod.DSL_EXEC_HANDLERS["plan_next_war_academy"](ctx2)
    assert ctx2.result["next"] is None
    assert ctx2.result["reason"] == "wa_gated"


@pytest.mark.asyncio
async def test_sync_war_academy_levels_noops_without_device(
    redis_async: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks import dsl_runtime

    def _no_runtime() -> Any:
        msg = "no device in unit test"
        raise RuntimeError(msg)

    monkeypatch.setattr(dsl_runtime, "bot_actions", _no_runtime)
    mod = _load_exec_module()
    ctx = DslExecContext(redis_client=redis_async, player_id="",
                         instance_id="bs1", args={}, result={})
    await mod.DSL_EXEC_HANDLERS["sync_war_academy_levels"](ctx)
    assert ctx.result["reason"] == "no_tiles_read"


@pytest.mark.asyncio
async def test_start_planned_war_academy_noops_without_plan(redis_async: Any) -> None:
    mod = _load_exec_module()
    await redis_async.delete("wos:instance:bs1:state")
    ctx = DslExecContext(redis_client=redis_async, player_id="",
                         instance_id="bs1", args={}, result={})
    await mod.DSL_EXEC_HANDLERS["start_planned_war_academy"](ctx)
    assert ctx.result["reason"] == "no_plan"
