"""Wiring guards for the onboarding overlay rules and scenarios.

Every analyze rule must point at a real area region and push a scenario that
resolves; every onboarding scenario's region steps must resolve to a known
region. This is the failure class the module is prone to — typos, dead refs,
and the stale ``analyze.yaml.off`` that used to ship ungated rules. Cheaper to
fail here than to watch a fresh account stall mid-tutorial.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from analysis.overlay_manifest import load_analyze_yaml
from analysis.overlay_rules import optional_push_scenario_tasks
from config.paths import repo_root
from config.startup_validation import (
    _load_merged_area_region_names,
    _overlay_rule_region_refs,
)
from dsl import template_resolver

_ONB = Path("games/wos/core/onboarding")
_REGION_STEP_KEYS = ("click", "long_click", "match", "while_match", "ocr")


def _onboarding_rules() -> list[dict]:
    doc = load_analyze_yaml(repo_root() / _ONB / "analyze" / "analyze.yaml")
    return [r for r in doc.get("overlay", []) if isinstance(r, dict)]


def _walk_region_refs(steps):
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        for key in _REGION_STEP_KEYS:
            val = step.get(key)
            if isinstance(val, str) and val:
                yield key, val
        yield from _walk_region_refs(step.get("steps"))


def test_onboarding_analyze_rules_reference_known_regions() -> None:
    regions = _load_merged_area_region_names(repo_root())
    missing = [
        (rule.get("name"), field, region)
        for rule in _onboarding_rules()
        for field, region in _overlay_rule_region_refs(rule)
        if region not in regions
    ]
    assert not missing, f"unknown regions in onboarding analyze: {missing}"


def test_onboarding_analyze_rules_are_phase_gated() -> None:
    # Every rule is device-level and gated to the pre-identity phase; an ungated
    # device-level rule spams the queue (the bug analyze.yaml.off carried).
    ungated = [
        rule.get("name")
        for rule in _onboarding_rules()
        if rule.get("device_level") is not True or rule.get("cond") != 'active_player == ""'
    ]
    assert not ungated, f"onboarding rules missing device-level phase gate: {ungated}"


def test_onboarding_analyze_push_targets_resolve() -> None:
    rr = repo_root()
    missing = []
    for rule in _onboarding_rules():
        for task in optional_push_scenario_tasks(rule):
            key = task.get("dsl_scenario") or task.get("type")
            if key and template_resolver.resolve(rr, str(key)) is None:
                missing.append((rule.get("name"), key))
    assert not missing, f"unresolved pushScenario targets: {missing}"


def test_onboarding_scenario_step_regions_resolve() -> None:
    rr = repo_root()
    regions = _load_merged_area_region_names(rr)
    missing = []
    for path in sorted((rr / _ONB / "scenarios").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for field, region in _walk_region_refs(doc.get("steps")):
            if "{" in region:  # pointer-template placeholder, e.g. ${pointer}
                continue
            if region not in regions:
                missing.append((path.name, field, region))
    assert not missing, f"unknown regions in onboarding scenarios: {missing}"
