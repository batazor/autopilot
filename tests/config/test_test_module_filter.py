"""Regression tests for the per-instance ``test_module`` queue filter.

Bug: cron/planner rows carry their scenario in ``task_type`` (no
``dsl_scenario``), so the filter treated them as unbound framework work and
let every cron through — a single-module lock did nothing to the crons.
"""
from __future__ import annotations

import pytest

from config.paths import repo_root
from config.test_module import task_payload_allowed

RR = repo_root()


def _allowed(payload: dict, test_module: str) -> bool:
    return task_payload_allowed(payload, test_module=test_module, repo_root=RR)


def test_no_lock_allows_everything() -> None:
    assert _allowed({"task_type": "refresh_calendar"}, "") is True
    assert _allowed({"task_type": "refresh_calendar"}, "all") is True


@pytest.mark.parametrize(
    "task_type",
    [
        "refresh_calendar",          # → calendar module
        "alliance.war.auto_join",    # → alliance module
        "free_recruitments_today",   # → heroes module
        "scan_alliance_members",     # → unresolved (default-deny)
        "build.planner.pick_next_building",  # → unresolved (default-deny)
        "vip.daily",                 # → vip module
    ],
)
def test_cron_tasks_blocked_under_helper_lock(task_type: str) -> None:
    # The bug: these have no ``dsl_scenario`` and used to slip through.
    assert _allowed({"task_type": task_type}, "alliance/helper") is False


@pytest.mark.parametrize("task_type", ["popup", "reconnect", "welcome_back"])
def test_infrastructure_always_runs(task_type: str) -> None:
    assert _allowed({"task_type": task_type}, "alliance/helper") is True


def test_who_i_am_blocked_under_helper_lock() -> None:
    # Device-level help-icon tap needs no identity; the costly probe is blocked.
    assert _allowed({"task_type": "who_i_am"}, "alliance/helper") is False


def test_same_module_task_allowed() -> None:
    # When the lock targets the owning module, its task runs.
    assert _allowed({"task_type": "refresh_calendar"}, "calendar") is True


def test_unbound_framework_payload_passes() -> None:
    # No scenario binding at all (e.g. overlay-derived tap rows).
    assert _allowed({}, "alliance/helper") is True
