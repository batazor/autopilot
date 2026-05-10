from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_hand_pointer_scenarios_check_visibility_before_click() -> None:
    # Each file: explicit match-then-click prefix (visibility before tap).
    expected = {
        "hand_pointer.yaml": [
            {"match": "hand_pointer", "threshold": 0.80, "min_match_saturation": 30},
            {"click": "hand_pointer"},
        ],
        "hand_pointer_small.yaml": [
            {"match": "hand_pointer_small", "threshold": 0.75, "min_match_saturation": 40},
            {"click": "hand_pointer_small"},
        ],
        "hand_pointer_small_reverse.yaml": [
            {"match": "hand_pointer_small_reverse", "threshold": 0.9},
            {"click": "hand_pointer_small_reverse"},
        ],
    }

    for filename, want_prefix in expected.items():
        doc = yaml.safe_load((REPO / "scenarios/onboarding" / filename).read_text())
        assert doc["steps"][:2] == want_prefix
