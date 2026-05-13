from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_hand_pointer_scenarios_check_visibility_before_click() -> None:
    """Each tutorial-hand scenario must guard the tap on a visibility check.

    All hand-pointer scenarios use the ``while_match: max: 1`` form — a soft
    guard that skips the inner click cleanly when the region isn't visible,
    instead of aborting like ``match:`` would.
    """
    expected_while_match_form: dict[str, dict] = {
        "hand_pointer.yaml": {
            "while_match": "hand_pointer",
            "max": 1,
            "steps": [{"click": "hand_pointer"}],
        },
        "hand_pointer_small.yaml": {
            "while_match": "hand_pointer_small",
            "max": 1,
            "steps": [{"click": "hand_pointer_small"}],
        },
        "hand_pointer_small_reverse.yaml": {
            "while_match": "hand_pointer_small_reverse",
            "max": 1,
            "steps": [{"click": "hand_pointer_small_reverse"}],
        },
    }

    for filename, want_first_step in expected_while_match_form.items():
        doc = yaml.safe_load((REPO / "scenarios/onboarding" / filename).read_text())
        assert doc["steps"][0] == want_first_step, filename
