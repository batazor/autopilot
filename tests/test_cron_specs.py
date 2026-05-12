"""Scenario discovery: ``cron`` field vs plain DSL files."""

from __future__ import annotations

from pathlib import Path

from scenarios.cron_specs import (
    iter_cron_yaml_files,
    iter_plain_scenario_yaml_files,
    iter_scenarios_yaml_paths,
)
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY
from scheduler.runner import resolve_cron_priority


def test_cron_and_plain_partition_repo_scenarios() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scenarios_root = repo_root / "scenarios"
    all_yaml = set(iter_scenarios_yaml_paths(scenarios_root))
    cron_set = set(iter_cron_yaml_files(scenarios_root))
    plain_set = set(iter_plain_scenario_yaml_files(scenarios_root))
    assert cron_set.isdisjoint(plain_set)
    assert cron_set | plain_set == all_yaml


def test_cron_default_matches_overlay_default() -> None:
    """Cron-scheduled scenarios use the same default priority as overlay-pushed
    ones — a scenario's importance is a property of what it does, not of how
    it was scheduled. Both fall back to ``DEFAULT_SCENARIO_PRIORITY``."""
    assert DEFAULT_SCENARIO_PRIORITY == 80_000


def test_resolve_cron_priority_returns_explicit_int() -> None:
    assert resolve_cron_priority(52) == 52
    assert resolve_cron_priority(10) == 10
    assert resolve_cron_priority("75") == 75  # YAML may quote ints
    # ``0`` is now a valid explicit choice distinct from "missing".
    assert resolve_cron_priority(0) == 0


def test_resolve_cron_priority_fallback_for_missing_or_invalid() -> None:
    assert resolve_cron_priority(None) == DEFAULT_SCENARIO_PRIORITY
    # bool is a subclass of int — the previous ``int(... or 1)`` silently
    # converted ``True`` → 1, ``False`` → 1; treat both as "no explicit value".
    assert resolve_cron_priority(True) == DEFAULT_SCENARIO_PRIORITY
    assert resolve_cron_priority(False) == DEFAULT_SCENARIO_PRIORITY
    assert resolve_cron_priority("not-a-number") == DEFAULT_SCENARIO_PRIORITY
    assert resolve_cron_priority([]) == DEFAULT_SCENARIO_PRIORITY
