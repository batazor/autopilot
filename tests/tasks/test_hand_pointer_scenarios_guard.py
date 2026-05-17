from __future__ import annotations

from pathlib import Path

import pytest

from scenarios import template_resolver

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "scenario_key",
    [
        "onboarding.click.hand_pointer",
        "onboarding.click.hand_pointer_small",
        "onboarding.click.hand_pointer_small_reverse",
    ],
)
def test_hand_pointer_scenarios_check_visibility_before_click(
    snapshot,
    scenario_key: str,
) -> None:
    """Each tutorial-hand scenario must guard the tap on a visibility check.

    All hand-pointer scenarios use the ``while_match: max: 1`` form — a soft
    guard that skips the inner click cleanly when the region isn't visible,
    instead of aborting like ``match:`` would.
    """
    loaded = template_resolver.load_doc(REPO, scenario_key)
    assert loaded is not None
    _path, doc = loaded
    assert doc == snapshot
