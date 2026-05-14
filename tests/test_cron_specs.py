"""Scenario discovery: ``cron`` field vs plain DSL files."""

from __future__ import annotations

from pathlib import Path

from scenarios.cron_specs import (
    iter_cron_yaml_files,
    iter_plain_scenario_yaml_files,
    iter_scenarios_yaml_paths,
    resolve_cron_priority,
    resolve_cron_task_type,
)
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY


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


def test_resolve_cron_task_type_prefers_explicit_task() -> None:
    """``task:`` is the canonical override knob — scheduler reads it first
    so the UI Cron Push must agree."""
    p = Path("scenarios/by_cron/check_main_city.yaml")
    assert resolve_cron_task_type({"task": "main_city_check"}, p) == "main_city_check"
    # ``task_type:`` is the legacy alias — still honored second.
    assert resolve_cron_task_type({"task_type": "legacy_name"}, p) == "legacy_name"


def test_resolve_cron_task_type_falls_back_to_stem() -> None:
    """Most cron YAMLs in the repo declare ``cron:`` + ``steps:`` and rely
    on the stem fallback. The UI Cron Push used to render an empty ``task``
    cell and refuse to push these — must now resolve to the stem."""
    p = Path("scenarios/by_cron/check_main_city.yaml")
    assert resolve_cron_task_type({}, p) == "check_main_city"
    assert resolve_cron_task_type({"task": ""}, p) == "check_main_city"
    assert resolve_cron_task_type({"task": "   "}, p) == "check_main_city"


def test_real_repo_cron_yamls_all_resolve() -> None:
    """Repo-wide invariant: every cron YAML resolves to a non-empty task —
    if a new cron file lands without ``task:`` *and* a usable stem (e.g.
    ``.yaml`` only), it must trip this test instead of silently failing
    every Push from the UI."""
    import yaml
    repo_root = Path(__file__).resolve().parents[1]
    scenarios_root = repo_root / "scenarios"
    for p in iter_cron_yaml_files(scenarios_root):
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        assert resolve_cron_task_type(raw, p), (
            f"cron YAML {p.relative_to(repo_root)} resolves to empty task"
        )


def test_ui_and_scheduler_share_resolvers() -> None:
    """Both helpers live in :mod:`scenarios.cron_specs` — scheduler imports
    them from there, and the UI Cron Push file is wired the same way (the
    Streamlit page itself isn't imported here because of its module-level
    ``st.title`` and similar runtime calls). Pin the names so accidental
    re-defines in either consumer don't fork the behaviour again.
    """
    import ast

    from scenarios.cron_specs import (
        resolve_cron_priority as cs_prio,
    )
    from scenarios.cron_specs import (
        resolve_cron_task_type as cs_task,
    )
    from scheduler.runner import resolve_cron_priority as sr_prio
    from scheduler.runner import resolve_cron_task_type as sr_task

    assert cs_prio is sr_prio
    assert cs_task is sr_task

    # Parse the UI page module without importing it — looking for the import
    # of both helpers from ``scenarios.cron_specs`` (no local re-define).
    page_src = (
        Path(__file__).resolve().parents[1] / "ui" / "views" / "scenarios.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(page_src)
    cron_specs_imports: set[str] = set()
    redefines: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "scenarios.cron_specs":
            cron_specs_imports.update(a.name for a in node.names)
        if isinstance(node, ast.FunctionDef) and node.name in {
            "resolve_cron_priority", "resolve_cron_task_type",
        }:
            redefines.add(node.name)
    assert "resolve_cron_priority" in cron_specs_imports
    assert "resolve_cron_task_type" in cron_specs_imports
    assert redefines == set(), (
        f"UI page re-defines resolver(s) instead of importing: {redefines}"
    )
