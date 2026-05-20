"""Structural checks for tab-specific mail claim scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from dsl import template_resolver

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
TAB_SCENARIO_KEYS = (
    "mail.claim.wars",
    "mail.claim.alliance",
    "mail.claim.system",
    "mail.claim.reports",
    "mail.claim.starred",
)


def test_legacy_generic_mail_claim_scenario_removed() -> None:
    assert not (MODULE_DIR / "scenarios" / "mail.claim.yaml").exists()


def test_literal_tab_claim_copies_removed() -> None:
    for tab in ("wars", "alliance", "system", "reports", "starred"):
        assert not (MODULE_DIR / "scenarios" / f"mail.claim.{tab}.yaml").exists()


@pytest.mark.parametrize("scenario_key", TAB_SCENARIO_KEYS)
def test_tab_template_renders_explicit_mail_pages(snapshot, scenario_key: str) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, scenario_key)
    assert loaded is not None
    path, doc = loaded
    assert path.name == "mail.claim.{tab}.yaml"
    assert doc == snapshot
