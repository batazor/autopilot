"""Loader failures must be recorded, not just logged — they feed the
dashboard red banner via ``dashboard.load_failures``."""
from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from dsl.loader import ScenarioLoader

if TYPE_CHECKING:
    from pathlib import Path


def _write_valid(scenarios_dir: Path) -> None:
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


def test_malformed_yaml_is_recorded_and_others_still_load(tmp_path: Path) -> None:
    _write_valid(tmp_path)
    broken = tmp_path / "broken.yaml"
    broken.write_text("steps: [unclosed\n", encoding="utf-8")

    loader = ScenarioLoader(tmp_path)

    assert [s.name for s in loader.load_all()] == ["Regular"]
    failures = loader.load_failures()
    assert len(failures) == 1
    assert failures[0]["file"] == str(broken)
    assert failures[0]["error"]
    assert failures[0]["ts"] > 0


def test_invalid_declarative_schema_is_recorded(tmp_path: Path) -> None:
    bad = tmp_path / "bad_schema.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "kind": "scenario",
                "enabled": True,
                "name": "Bad",
                "steps": [{"task": "daily_checkin"}],  # missing cooldown
            }
        ),
        encoding="utf-8",
    )

    loader = ScenarioLoader(tmp_path)

    assert loader.load_all() == []
    failures = loader.load_failures()
    assert len(failures) == 1
    assert failures[0]["file"] == str(bad)
    assert "task" in failures[0]["error"] and "cooldown" in failures[0]["error"]


def test_imperative_dsl_doc_is_not_a_failure(tmp_path: Path) -> None:
    (tmp_path / "imperative.yaml").write_text(
        yaml.safe_dump(
            {
                "enabled": True,
                "name": "Imperative",
                "steps": [{"match": "popup_close", "steps": [{"click": "popup_close"}]}],
            }
        ),
        encoding="utf-8",
    )

    loader = ScenarioLoader(tmp_path)

    assert loader.load_all() == []
    assert loader.load_failures() == []


def test_failures_clear_after_fixed_reload(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("steps: [unclosed\n", encoding="utf-8")
    loader = ScenarioLoader(tmp_path)
    assert len(loader.load_failures()) == 1

    broken.unlink()
    _write_valid(tmp_path)
    loader.reload(fire_callback=False)

    assert loader.load_failures() == []
    assert [s.name for s in loader.load_all()] == ["Regular"]
