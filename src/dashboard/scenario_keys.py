"""Scenario-key lookup helpers used by the FastAPI server and tests."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dsl.cron_specs import iter_scenarios_yaml_paths, load_root_mapping
from dsl.registry import scenario_roots


@lru_cache(maxsize=1)
def runnable_scenario_keys(repo_root_s: str) -> tuple[str, ...]:
    """Scenario keys the worker's ``DslScenarioTask`` can resolve via
    ``template_resolver.load_doc`` for a manual ``run_task`` command.

    A UI ``run_task`` bypasses ``_TASK_REGISTRY`` (intentionally empty since
    the old per-task registry was retired) and is dispatched as a DSL
    scenario keyed by ``task_type``. So the dropdown must list keys that
    have an actual YAML file behind them — otherwise the worker enqueues
    the manual task and immediately fails it with ``scenario_not_found``.
    Templates (``{hero}.yaml``) are excluded; the Debug Runner page expands
    those per hero.

    Disabled and cron-delegating docs are also excluded: the worker rejects
    them with ``invalid_steps`` (see ``tasks/dsl_scenario_execute_mixin.py``
    where ``steps`` must be a list — empty is fine, missing is not), and the
    scheduler skips ``enabled: false`` the same way (``scheduler/runner.py``).
    A cron-only YAML with no ``steps`` and just ``cron: + task: arena_check``
    would otherwise leak into this list and silently fail when an operator
    picks it.
    """
    repo_root = Path(repo_root_s)
    out: set[str] = set()
    for scen_root in scenario_roots(repo_root):
        for p in iter_scenarios_yaml_paths(scen_root.path):
            if "{" in p.name:
                continue
            raw = load_root_mapping(p)
            if raw is None:
                continue
            if not bool(raw.get("enabled", True)):
                continue
            steps = raw.get("steps")
            if not isinstance(steps, list):
                continue
            out.add(p.stem)
    return tuple(sorted(out))
