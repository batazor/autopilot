from __future__ import annotations

from pathlib import Path

import yaml

from scenarios.loader import ScenarioLoader, _is_dsl_scenario_doc


def test_dsl_scenario_doc_detects_repeat_first_step() -> None:
    raw = {
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

    assert _is_dsl_scenario_doc(raw) is True


def test_scenario_loader_skips_repeat_dsl_yaml(tmp_path: Path) -> None:
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
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
