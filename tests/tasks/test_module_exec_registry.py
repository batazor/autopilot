"""Every ``exec:`` name a scenario uses must resolve to a real handler.

Startup validation checks region names, ``push_scenario`` targets, cron tasks and
edge taps — but NOT handler identity, so a typo'd ``exec:`` used to survive to the
device. It now fails the step at runtime (``unknown_exec``, see
``tasks/dsl_scenario_helpers._exec_result_failure_reason``); this test moves the
detection all the way back to CI.

This replaces the per-module ``test_handler_is_registered`` one-liners: those
asserted one name each, and each sat directly above a test that invoked the same
handler and would have raised ``KeyError`` anyway. Sweeping every scenario is
strictly stronger and does not need a new test per handler.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tasks.dsl_exec import DSL_EXEC_REGISTRY, build_dsl_exec_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Filename placeholders (``level_up_{hero}.yaml``) also appear inside step
# values; a templated exec name cannot be resolved statically.
_PLACEHOLDER = re.compile(r"[{$]")


def _iter_exec_names() -> list[tuple[str, str]]:
    """``(scenario_path, exec_name)`` for every ``exec:`` step in an ENABLED scenario.

    Disabled scenarios are skipped: a parked scenario legitimately points at a
    handler whose own module is parked too (``advance_chapter_objective`` →
    ``route_chapter_objective``, both switched off together). Requiring those to
    resolve would force the dead module's ``exec.py`` to stay loaded.
    """
    from dsl.registry import iter_scenario_yaml_files

    out: list[tuple[str, str]] = []

    def walk(node: object, rel: str) -> None:
        if isinstance(node, dict):
            raw = node.get("exec")
            if isinstance(raw, str) and raw.strip():
                out.append((rel, raw.strip()))
            for value in node.values():
                walk(value, rel)
        elif isinstance(node, list):
            for item in node:
                walk(item, rel)

    for _root, path in iter_scenario_yaml_files(_REPO_ROOT):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # a malformed YAML is startup validation's problem
            continue
        if isinstance(doc, dict) and doc.get("enabled") is False:
            continue
        walk(doc, path.relative_to(_REPO_ROOT).as_posix())
    return out


def test_every_scenario_exec_name_resolves() -> None:
    registry = build_dsl_exec_registry(_REPO_ROOT)
    names = _iter_exec_names()
    assert names, "found no exec: steps at all — the scenario sweep is broken"

    unknown = sorted(
        {
            f"{rel}: {name}"
            for rel, name in names
            if not _PLACEHOLDER.search(name) and name not in registry
        }
    )
    assert not unknown, "scenarios reference exec handlers that do not exist:\n" + "\n".join(
        unknown
    )


def test_registered_handlers_are_callable() -> None:
    registry = build_dsl_exec_registry(_REPO_ROOT)
    assert registry, "the exec registry is empty"
    not_callable = sorted(k for k, v in registry.items() if not callable(v))
    assert not not_callable


@pytest.mark.parametrize(
    "name",
    [
        "gift_code_scrape",
        "gift_code_redeem",
        "kingshot_gift_code_scrape",
        "kingshot_gift_code_redeem",
    ],
)
def test_gift_code_handlers_reach_the_singleton_registry(name: str) -> None:
    """Gift codes are merged into the core registry directly rather than
    discovered through a ``module.yaml`` — a separate mechanism from every other
    handler, so the singleton is worth pinning on its own."""
    assert name in DSL_EXEC_REGISTRY
