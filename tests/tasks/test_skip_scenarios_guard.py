from __future__ import annotations

from pathlib import Path

from dsl import template_resolver

REPO = Path(__file__).resolve().parents[2]


def test_skip_button_scenario_checks_visibility_before_click(snapshot) -> None:
    loaded = template_resolver.load_doc(REPO, "onboarding.click.skip_button")
    assert loaded is not None
    _path, doc = loaded
    assert doc == snapshot
