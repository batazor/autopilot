from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _read_skip_scenario(rel_under_onboarding: str) -> dict:
    p = REPO / "modules" / "draft" / "core" / "pop-up" / "scenarios" / rel_under_onboarding
    if p.is_file():
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(rel_under_onboarding)


def test_skip_button_scenario_checks_visibility_before_click() -> None:
    doc = _read_skip_scenario("skip_button.yaml")

    assert doc["steps"][:2] == [
        {"match": "skip_button", "threshold": 0.95},
        {"click": "skip_button"},
    ]


def test_skip_text_button_scenario_checks_visibility_before_click() -> None:
    doc = _read_skip_scenario("skip_text_button.yaml")

    # ``while_match`` with the click nested inside is the visibility guard:
    # the inner steps only run when ``skip_text_button`` is on screen.
    assert doc["steps"][0] == {
        "while_match": "skip_text_button",
        "max": 1,
        "steps": [
            {"click": "skip_text_button"},
            {"wait": "2s"},
        ],
    }
