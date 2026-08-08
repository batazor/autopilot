"""Operator ``WOS_SCENARIOS`` allowlist over scenario discovery.

The scenario-key sibling of ``WOS_MODULES``: modules (and therefore regions,
routes and overlay rules) stay loaded, only the runnable scenario set narrows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsl import registry as reg
from dsl import template_resolver as tr


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    reg._clear_scenario_allowlist_cache()
    tr._clear_template_resolver_caches()
    yield
    reg._clear_scenario_allowlist_cache()
    tr._clear_template_resolver_caches()


def test_no_env_keeps_all_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WOS_SCENARIOS", raising=False)
    reg._clear_scenario_allowlist_cache()
    assert reg.scenario_allowlist() is None
    assert reg._scenario_allowed(Path("scenarios/anything.yaml")) is True


def test_empty_env_is_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_SCENARIOS", "  ,  ")
    reg._clear_scenario_allowlist_cache()
    assert reg.scenario_allowlist() is None
    assert reg._scenario_allowed(Path("scenarios/anything.yaml")) is True


def test_allowlist_keeps_only_listed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_SCENARIOS", "intel_run,intel_lighthouse")
    reg._clear_scenario_allowlist_cache()
    assert reg._scenario_allowed(Path("games/wos/intel/scenarios/intel_run.yaml")) is True
    assert reg._scenario_allowed(Path("x/intel_lighthouse.yaml")) is True
    assert reg._scenario_allowed(Path("x/check_main_city.yaml")) is False


def test_dotted_cron_stem_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """``intel_claim.cron.yaml`` has stem ``intel_claim.cron`` — not ``intel_claim``."""
    monkeypatch.setenv("WOS_SCENARIOS", "intel_claim.cron")
    reg._clear_scenario_allowlist_cache()
    assert reg._scenario_allowed(Path("by_cron/intel_claim.cron.yaml")) is True
    assert reg._scenario_allowed(Path("by_cron/intel_claim.yaml")) is False


def test_entries_are_case_and_suffix_forgiving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_SCENARIOS", "Intel_Run.yaml, INTEL_LIGHTHOUSE")
    reg._clear_scenario_allowlist_cache()
    assert reg._scenario_allowed(Path("x/intel_run.yaml")) is True
    assert reg._scenario_allowed(Path("x/intel_lighthouse.yaml")) is True


def test_iter_scenario_yaml_files_respects_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.paths import repo_root

    monkeypatch.setenv("WOS_SCENARIOS", "intel_run")
    reg._clear_scenario_allowlist_cache()
    stems = {p.stem for _root, p in reg.iter_scenario_yaml_files(repo_root())}
    assert stems <= {"intel_run"}


def test_resolver_hides_filtered_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver is what turns a queued key into a path — a key outside the
    slice must not resolve, so the task ends ``scenario_not_found``."""
    from config.paths import repo_root

    monkeypatch.setenv("WOS_SCENARIOS", "intel_run")
    reg._clear_scenario_allowlist_cache()
    tr._clear_template_resolver_caches()
    keys = {rk.key for rk in tr.iter_resolved_keys(repo_root())}
    assert keys <= {"intel_run"}


def test_cross_ref_severity_downgrades_under_scenario_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slice legitimately strands overlay ``pushScenario`` targets; those must
    warn, not hard-fail the boot."""
    from config import module_discovery as md
    from config.startup_validation import _cross_ref_severity

    monkeypatch.delenv("WOS_MODULES", raising=False)
    monkeypatch.delenv("WOS_SCENARIOS", raising=False)
    md._clear_module_discovery_caches()
    reg._clear_scenario_allowlist_cache()
    assert _cross_ref_severity() == "error"

    monkeypatch.setenv("WOS_SCENARIOS", "intel_run")
    reg._clear_scenario_allowlist_cache()
    assert _cross_ref_severity() == "warning"
