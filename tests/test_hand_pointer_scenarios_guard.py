from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_hand_pointer_scenarios_check_visibility_before_click() -> None:
    expected = {
        "hand_pointer.yaml": ("hand_pointer", 0.98),
        "hand_pointer_small.yaml": ("hand_pointer_small", 0.85),
        "hand_pointer_small_reverse.yaml": ("hand_pointer_small_reverse", 0.92),
    }

    for filename, (region, threshold) in expected.items():
        doc = yaml.safe_load((REPO / "scenarios/onboarding" / filename).read_text())
        assert doc["steps"][:2] == [
            {"match": region, "threshold": threshold, "min_match_saturation": 40},
            {"click": region},
        ]
