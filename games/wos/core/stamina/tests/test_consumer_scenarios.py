"""Contract test: every stamina demand resolves to a loadable consumer scenario.

The allocator enqueues a demand's ``task_type`` as the scenario key, so each
demand must map to exactly one enabled scenario YAML somewhere under
``games/wos/``. Guards against the budget table drifting away from the
scenarios it drives (a renamed/missing consumer would silently no-op).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from games.wos.core.stamina.model import Budget

REPO_ROOT = Path(__file__).resolve().parents[5]
WOS_ROOT = REPO_ROOT / "games" / "wos"
BUDGET = Budget.load()


def _scenario_files(task_type: str) -> list[Path]:
    return [
        p
        for p in WOS_ROOT.rglob(f"{task_type}.yaml")
        if p.parent.name == "scenarios" and "drafts" not in p.parts
    ]


def test_every_demand_has_one_enabled_scenario():
    for demand in BUDGET.demands:
        files = _scenario_files(demand.task_type)
        assert len(files) == 1, (
            f"demand {demand.id!r} → task_type {demand.task_type!r} "
            f"must map to exactly one scenario, found {files}"
        )
        doc = yaml.safe_load(files[0].read_text(encoding="utf-8"))
        assert doc.get("enabled") is True, f"{files[0]} must be enabled to run"


def test_every_supply_has_one_enabled_scenario():
    for supply in BUDGET.supplies:
        files = _scenario_files(supply.task_type)
        assert len(files) == 1, (
            f"supply {supply.id!r} → task_type {supply.task_type!r} "
            f"must map to exactly one scenario, found {files}"
        )
        doc = yaml.safe_load(files[0].read_text(encoding="utf-8"))
        assert doc.get("enabled") is True, f"{files[0]} must be enabled to run"


def test_stub_consumer_task_types_present():
    # The four stubs created for this milestone (3 demands + 1 supply).
    declared = {d.task_type for d in BUDGET.demands}
    declared |= {s.task_type for s in BUDGET.supplies}
    assert {"intel_run", "joe_hunt_bandits", "beast_hunt", "pet_stamina_skill"} <= declared
