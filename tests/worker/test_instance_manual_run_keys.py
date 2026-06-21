"""``run_task`` from the Instance page (``ui/views/instance.py``) bypasses
``_TASK_REGISTRY`` (intentionally empty) and dispatches as a DSL scenario
keyed by ``task_type``. The UI dropdown therefore must list keys that the
worker can actually resolve — otherwise the manual queue item fires and
immediately fails with ``scenario_not_found``.

These tests pin the helper that builds the dropdown so the contract holds
even when ``settings.yaml`` is reshaped further down the line.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dashboard.scenario_keys import runnable_scenario_keys
from dsl import template_resolver as _tmpl
from dsl.cron_specs import iter_cron_yaml_files_for_repo

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manual_run_keys_includes_known_scenario() -> None:
    """``who_i_am`` must be pickable so an operator can re-run identity from the UI."""
    keys = runnable_scenario_keys(str(REPO_ROOT))
    assert "who_i_am" in keys


def test_manual_run_keys_excludes_templates() -> None:
    """Hero-template files like ``level_up_{hero}.yaml`` aren't runnable as-is
    — they need ``{hero}`` substituted. The Debug Runner page expands them
    per hero; this dropdown lists only directly-runnable keys."""
    keys = runnable_scenario_keys(str(REPO_ROOT))
    assert "{hero}" not in keys
    assert "level_up_{hero}" not in keys
    # Sanity: hero template exists under a module scenarios tree.
    hero_tpl = REPO_ROOT / "games/wos/heroes/heroes/scenarios/{hero}.yaml"
    assert hero_tpl.is_file(), f"missing hero template: {hero_tpl}"


def test_manual_run_keys_excludes_drafts() -> None:
    """Draft scenario YAMLs are placeholder schemas.

    ``run_task`` for them would either fail or do nothing — and the underlying
    loader excludes them anyway, so the UI must mirror that.
    """
    keys = runnable_scenario_keys(str(REPO_ROOT))
    draft_stems = {
        p.stem
        for root in (REPO_ROOT / "modules").glob("**/scenarios")
        for p in (root / "drafts").glob("*.yaml")
    }
    leaked = draft_stems & set(keys)
    assert leaked == set(), f"draft scenarios leaked into manual-run list: {leaked}"


def test_every_manual_run_key_resolves_via_template_resolver() -> None:
    """Final guarantee: every key the UI offers must be loadable by the
    worker. If this fails, the operator gets ``scenario_not_found`` — the
    exact bug this helper exists to prevent."""
    keys = runnable_scenario_keys(str(REPO_ROOT))
    unresolved = [k for k in keys if _tmpl.resolve(REPO_ROOT, k) is None]
    assert unresolved == [], f"manual-run keys with no resolver hit: {unresolved}"


def test_manual_run_keys_excludes_cron_delegating_scenarios() -> None:
    """Cron-only specs without ``steps:`` delegate work to a separate task type.

    The resolver finds them, but the worker rejects them at runtime with
    ``invalid_steps``. The dropdown must exclude any such doc so operators
    can't queue a guaranteed failure.
    """
    keys = runnable_scenario_keys(str(REPO_ROOT))
    check_arena = [p for p in iter_cron_yaml_files_for_repo(REPO_ROOT) if p.stem == "check_arena"]
    if check_arena:
        assert "check_arena" not in keys


def test_manual_run_keys_excludes_disabled() -> None:
    """``enabled: false`` mirrors the scheduler's own filter
    (``scheduler/runner.py``). Surfacing a disabled scenario in the dropdown
    misleads the operator — clicking ``Queue task`` would enqueue something
    the author explicitly turned off."""
    keys = runnable_scenario_keys(str(REPO_ROOT))
    for k in keys:
        resolved = _tmpl.resolve(REPO_ROOT, k)
        if resolved is None:
            continue
        raw = yaml.safe_load(resolved.path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            assert bool(raw.get("enabled", True)), (
                f"disabled scenario leaked into manual-run list: {k}"
            )


def test_every_manual_run_key_has_steps_list() -> None:
    """The worker's ``DslScenarioTask`` returns ``invalid_steps`` when
    ``steps`` isn't a list (see ``tasks/dsl_scenario_execute_mixin.py:91``).
    An empty list is allowed — the executor just walks nothing and returns
    success — but a missing or non-list ``steps`` must be filtered out
    upstream so the operator doesn't get a guaranteed runtime failure."""
    keys = runnable_scenario_keys(str(REPO_ROOT))
    bad: list[str] = []
    for k in keys:
        resolved = _tmpl.resolve(REPO_ROOT, k)
        if resolved is None:
            bad.append(f"{k}: not resolvable")
            continue
        raw = yaml.safe_load(resolved.path.read_text(encoding="utf-8")) or {}
        steps = raw.get("steps") if isinstance(raw, dict) else None
        if not isinstance(steps, list):
            bad.append(f"{k}: steps missing or not a list")
    assert bad == [], (
        "manual-run keys whose `steps` is not a list — worker would reject "
        f"with invalid_steps: {bad}"
    )
