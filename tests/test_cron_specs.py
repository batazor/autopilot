"""Scenario discovery: ``cron`` field vs plain DSL files."""

from __future__ import annotations

from pathlib import Path

from scenarios.cron_specs import (
    iter_cron_yaml_files,
    iter_plain_scenario_yaml_files,
    iter_scenarios_yaml_paths,
)


def test_cron_and_plain_partition_repo_scenarios() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scenarios_root = repo_root / "scenarios"
    all_yaml = set(iter_scenarios_yaml_paths(scenarios_root))
    cron_set = set(iter_cron_yaml_files(scenarios_root))
    plain_set = set(iter_plain_scenario_yaml_files(scenarios_root))
    assert cron_set.isdisjoint(plain_set)
    assert cron_set | plain_set == all_yaml
