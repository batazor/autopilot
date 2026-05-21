"""Tests for scenario step flow graph builder."""

from __future__ import annotations

from dashboard.flow_layout import build_scenario_step_flow


def test_build_scenario_step_flow_running() -> None:
    summaries = ("tap mail", "read gifts", "back")
    nodes, edges, _h, _w = build_scenario_step_flow(
        summaries,
        current_step=1,
        is_running=True,
    )
    assert len(nodes) == 3
    assert len(edges) == 2
    assert nodes[0]["data"]["status"] == "success"
    assert nodes[1]["data"]["status"] == "loading"
    assert nodes[2]["data"]["status"] == "initial"


def test_build_scenario_step_flow_idle_start() -> None:
    summaries = ("a", "b", "c")
    nodes, _, _, _ = build_scenario_step_flow(
        summaries,
        is_running=False,
        idle_start_step=2,
    )
    assert nodes[0]["data"]["status"] == "success"
    assert nodes[2]["data"]["status"] == "loading"
