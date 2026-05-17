"""Structural checks for tab-specific mail claim scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from scenarios import template_resolver

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
TAB_SCENARIOS = {
    "mail.claim.wars": ("mail.wars", "Mail Wars: Claim Rewards"),
    "mail.claim.alliance": ("mail.alliance", "Mail Alliance: Claim Rewards"),
    "mail.claim.system": ("mail.system", "Mail System: Claim Rewards"),
    "mail.claim.reports": ("mail.reports", "Mail Reports: Claim Rewards"),
    "mail.claim.starred": ("mail.starred", "Mail Starred: Claim Rewards"),
}


def test_legacy_generic_mail_claim_scenario_removed() -> None:
    assert not (MODULE_DIR / "scenarios" / "mail.claim.yaml").exists()


def test_literal_tab_claim_copies_removed() -> None:
    for tab in ("wars", "alliance", "system", "reports", "starred"):
        assert not (MODULE_DIR / "scenarios" / f"mail.claim.{tab}.yaml").exists()


@pytest.mark.parametrize(
    "scenario_key,expected_node,expected_name",
    [
        (key, node, name)
        for key, (node, name) in TAB_SCENARIOS.items()
    ],
    ids=list(TAB_SCENARIOS),
)
def test_tab_template_renders_explicit_mail_pages(
    snapshot,
    scenario_key: str,
    expected_node: str,
    expected_name: str,
) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, scenario_key)
    assert loaded is not None
    path, doc = loaded
    assert path.name == "mail.claim.{tab}.yaml"
    assert doc["enabled"] is True
    assert doc["priority"] == 80_000
    assert doc["node"] == expected_node
    assert doc["name"] == expected_name
    assert doc == snapshot
