"""All committed scenario YAML must pass the DSL schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scenarios.dsl_schema import dump_scenario, parse_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_ROOT = REPO_ROOT / "scenarios"


def _runnable_yaml_files() -> list[Path]:
    return sorted(
        p
        for p in SCENARIOS_ROOT.rglob("*.yaml")
        if "drafts/" not in p.relative_to(SCENARIOS_ROOT).as_posix()
    )


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=lambda p: p.name)
def test_parse_existing_scenario(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    assert scenario.name, f"missing name in {path}"


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=lambda p: p.name)
def test_round_trip_preserves_structure(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    dumped = dump_scenario(scenario)
    reparsed = parse_scenario(dumped)
    assert reparsed.model_dump(by_alias=True, exclude_none=True) == scenario.model_dump(
        by_alias=True, exclude_none=True
    )
