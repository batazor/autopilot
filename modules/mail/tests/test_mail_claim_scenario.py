"""Structural checks for tab-specific mail claim scenarios."""

from __future__ import annotations

from pathlib import Path

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


def _is_claim_block_root(step: dict) -> bool:
    return (
        isinstance(step, dict)
        and step.get("while_match") == "mail.gift"
        and step.get("max") == 30
        and step.get("min_match_saturation") == 48
    )


def _assert_claim_block_shape(block: list, *, context: str) -> None:
    assert isinstance(block, list) and len(block) == 3, (
        f"{context}: claim block must have 3 steps (gift loop + claim_all + delete_all), "
        f"got {len(block) if isinstance(block, list) else type(block).__name__}"
    )
    assert _is_claim_block_root(block[0]), f"{context}: step 0 must be the mail.gift while_match"

    claim_guard = block[1]
    assert claim_guard.get("while_match") == "mail.claim.all"
    assert claim_guard.get("max") == 1
    claim_body = claim_guard.get("steps") or []
    assert any(s.get("click") == "mail.claim.all" for s in claim_body if isinstance(s, dict))

    delete_guard = block[2]
    assert delete_guard.get("while_match") == "mail.delete.all"
    assert delete_guard.get("max") == 1
    delete_body = delete_guard.get("steps") or []
    assert any(s.get("click") == "mail.delete.all" for s in delete_body if isinstance(s, dict))


def test_tab_template_renders_explicit_mail_pages() -> None:
    for scenario_key, (node, name) in TAB_SCENARIOS.items():
        loaded = template_resolver.load_doc(REPO_ROOT, scenario_key)
        assert loaded is not None
        path, doc = loaded
        assert path.name == "mail.claim.{tab}.yaml"
        assert doc["enabled"] is True
        assert doc["priority"] == 80_000
        assert doc["node"] == node
        assert doc["name"] == name
        _assert_claim_block_shape(doc["steps"], context=scenario_key)


def test_legacy_generic_mail_claim_scenario_removed() -> None:
    assert not (MODULE_DIR / "scenarios" / "mail.claim.yaml").exists()


def test_literal_tab_claim_copies_removed() -> None:
    for tab in ("wars", "alliance", "system", "reports", "starred"):
        assert not (MODULE_DIR / "scenarios" / f"mail.claim.{tab}.yaml").exists()
