from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_skip_button_scenario_checks_visibility_before_click() -> None:
    doc = yaml.safe_load((REPO / "scenarios/onboarding/skip_button.yaml").read_text())

    assert doc["steps"][:2] == [
        {"match": "skip_button", "threshold": 0.95},
        {"click": "skip_button"},
    ]


def test_skip_text_button_scenario_checks_visibility_before_click() -> None:
    doc = yaml.safe_load((REPO / "scenarios/onboarding/skip_text_button.yaml").read_text())

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
