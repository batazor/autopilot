from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from dsl.loader import ScenarioLoader, _is_declarative_scenario_doc

if TYPE_CHECKING:
    from pathlib import Path


def test_declarative_scenario_doc_requires_task_and_cooldown_steps() -> None:
    raw = {
        "enabled": True,
        "name": "Regular",
        "steps": [{"task": "daily_checkin", "cooldown": "1m"}],
    }

    assert _is_declarative_scenario_doc(raw) is True


def test_imperative_dsl_doc_is_not_declarative_scenario() -> None:
    raw = {
        "enabled": True,
        "name": "Chapter task router",
        "steps": [
            {
                "cond": 'chapter.task ~= "Shelter"',
                "steps": [{"click": "chapter.task"}],
            }
        ],
    }

    assert _is_declarative_scenario_doc(raw) is False


def test_scenario_loader_loads_only_declarative_yaml(tmp_path: Path) -> None:
    module_dir = tmp_path / "modules" / "core" / "test_scenarios"
    scenarios_dir = module_dir / "scenarios"
    scenarios_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenarios_dir / "upgrade.yaml").write_text(
        yaml.safe_dump(
            {
                "enabled": True,
                "name": "Upgrade",
                "steps": [
                    {
                        "repeat": {
                            "max": 10,
                            "steps": [{"while_match": "upgrade_button"}],
                        }
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (scenarios_dir / "regular.yaml").write_text(
        yaml.safe_dump(
            {
                "enabled": True,
                "name": "Regular",
                "steps": [{"task": "daily_checkin", "cooldown": "1m"}],
            }
        ),
        encoding="utf-8",
    )

    loaded = ScenarioLoader(scenarios_dir).load_all()

    assert [s.name for s in loaded] == ["Regular"]
