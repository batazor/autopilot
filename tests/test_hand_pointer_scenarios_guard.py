from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_hand_pointer_scenarios_check_visibility_before_click() -> None:
    """Each tutorial-hand scenario must guard the tap on a visibility check.

    Two shapes are accepted because hand-pointer cleanup migrated some
    scenarios from ``match:`` (hard guard, aborts on miss) to
    ``while_match: max: 1`` (soft guard, skips inner click cleanly):

    * ``[{"match": <reg>, ...}, {"click": <reg>}]`` — classic match-then-click
    * ``[{"while_match": <reg>, "max": 1, "steps": [{"click": <reg>}]}]`` —
      same intent, no abort on miss
    """
    expected_match_form: dict[str, list[dict]] = {
        "hand_pointer.yaml": [
            {"match": "hand_pointer", "threshold": 0.80, "min_match_saturation": 30},
            {"click": "hand_pointer"},
        ],
        "hand_pointer_small.yaml": [
            {"match": "hand_pointer_small", "threshold": 0.9, "min_match_saturation": 50},
            {"click": "hand_pointer_small"},
        ],
    }
    expected_while_match_form: dict[str, dict] = {
        "hand_pointer_small_reverse.yaml": {
            "while_match": "hand_pointer_small_reverse",
            "max": 1,
            "steps": [{"click": "hand_pointer_small_reverse"}],
        },
    }

    for filename, want_prefix in expected_match_form.items():
        doc = yaml.safe_load((REPO / "scenarios/onboarding" / filename).read_text())
        assert doc["steps"][:2] == want_prefix, filename

    for filename, want_first_step in expected_while_match_form.items():
        doc = yaml.safe_load((REPO / "scenarios/onboarding" / filename).read_text())
        assert doc["steps"][0] == want_first_step, filename
