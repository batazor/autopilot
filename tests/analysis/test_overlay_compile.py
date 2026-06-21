"""Compiled overlay plan: action normalization and screen gates."""

from __future__ import annotations

from analysis.overlay_compile import (
    ScreenGate,
    compile_overlay_plan,
    compile_overlay_rule,
)
from analysis.overlay_manifest import (
    clear_merged_analyze_yaml_cache,
    compiled_overlay_plan,
)
from config.paths import repo_root


def test_normalize_red_dot_action_from_is_red_dot() -> None:
    compiled = compile_overlay_rule(
        {
            "name": "dot.rule",
            "region": "foo",
            "screens": ["main_city"],
            "isRedDot": True,
        }
    )
    assert compiled is not None
    assert compiled.action == "red_dot"
    assert compiled.logical_name == "dot.rule"
    assert compiled.push_tasks == []
    assert compiled.screen.has_gate is True
    assert "main_city" in compiled.screen.allowed_lc


def test_screen_gate_glob_and_none() -> None:
    gate = ScreenGate(
        allowed_lc=frozenset({"page.heroes.*", "none"}),
        glob_patterns=("page.heroes.*",),
        wants_unknown=True,
        has_gate=True,
    )
    assert gate.allows("page.heroes.unit_01") is True
    assert gate.allows("") is True
    assert gate.allows("shop") is False


def test_compile_plan_skips_rules_without_name() -> None:
    plan = compile_overlay_plan(
        [{"name": "a", "action": "text", "region": "r"}, {"region": "orphan"}]
    )
    assert [r.logical_name for r in plan] == ["a"]


def test_compiled_overlay_plan_cached_with_merged_yaml() -> None:
    clear_merged_analyze_yaml_cache()
    root = repo_root()
    first = compiled_overlay_plan(root)
    second = compiled_overlay_plan(root)
    assert second is first
    assert len(first.rules) > 0
