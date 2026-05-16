"""Structural checks for ``modules/mail/scenarios/mail.claim.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]
SCENARIO_PATH = MODULE_DIR / "scenarios" / "mail.claim.yaml"
EXPECTED_TABS = (
    "mail.tab.wars",
    "mail.tab.alliance",
    "mail.tab.system",
    "mail.tab.reports",
    "mail.tab.starred",
)


def _load() -> dict:
    return yaml.safe_load(SCENARIO_PATH.read_text(encoding="utf-8"))


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


def test_scenario_node_priority_and_key() -> None:
    doc = _load()
    assert doc["node"] == "mail"
    assert doc["priority"] == 80_000
    assert doc["enabled"] is True
    assert SCENARIO_PATH.name == "mail.claim.yaml"


def test_six_top_level_steps_active_plus_five_tabs() -> None:
    steps = _load()["steps"]
    assert isinstance(steps, list)
    assert len(steps) == 6


def test_step0_is_bare_active_tab_claim_block() -> None:
    step = _load()["steps"][0]
    assert isinstance(step, dict)
    assert set(step.keys()) == {"steps"}
    _assert_claim_block_shape(step["steps"], context="step 0")


def test_each_tab_branch_has_layered_guards() -> None:
    steps = _load()["steps"]
    for branch_idx, tab_name in enumerate(EXPECTED_TABS, start=1):
        branch = steps[branch_idx]
        assert isinstance(branch, dict)
        assert branch.get("while_match") == tab_name
        assert branch.get("isTabActive") is False
        assert branch.get("max") == 1

        inner = branch.get("steps")
        assert isinstance(inner, list) and len(inner) == 1
        red = inner[0]
        assert red.get("while_match") == tab_name
        assert red.get("isRedDot") is True
        assert red.get("max") == 1

        body = red.get("steps")
        assert isinstance(body, list) and len(body) >= 3
        assert body[0].get("click") == tab_name
        assert "wait" in body[1]
        claim_group = body[-1]
        assert set(claim_group.keys()) == {"steps"}
        _assert_claim_block_shape(claim_group["steps"], context=f"branch {branch_idx} ({tab_name})")


def test_claim_block_is_shared_via_yaml_anchor() -> None:
    steps = _load()["steps"]
    usages = [steps[0]["steps"]]
    for i in range(1, 6):
        body = steps[i]["steps"][0]["steps"]
        usages.append(body[-1]["steps"])
    first = usages[0]
    for other in usages[1:]:
        assert other is first
