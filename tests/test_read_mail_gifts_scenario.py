"""Structural checks for ``scenarios/mail/read_mail_gifts.yaml``.

The scenario fans the same claim-loop over all 5 mail tabs. Pure YAML-level
checks (no bot run) lock the topology so a careless edit cannot silently:

* drop a tab branch;
* lose the active-tab claim step (which scans ``mail_gift`` directly because
  the active tab's red-dot is already cleared);
* swap the per-branch guards so we tap an already-active tab, or skip a
  red-dot we should have processed;
* regress to bare ``match:`` which would abort the scenario on the first
  guard miss instead of falling through to subsequent branches.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = REPO_ROOT / "scenarios" / "mail" / "read_mail_gifts.yaml"

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
    """The mail_gift while_match — first element of the claim block."""
    return (
        isinstance(step, dict)
        and step.get("while_match") == "mail_gift"
        and step.get("max") == 30
        and step.get("min_match_saturation") == 48
    )


def _assert_claim_block_shape(block: list, *, context: str) -> None:
    """The claim block is a 3-step sequence:

    1. ``while_match: mail_gift`` — drain individual gift mails;
    2. ``while_match: mail.claim_all / max: 1`` — list-level bulk claim;
    3. ``while_match: mail.delete_all / max: 1`` — list-level cleanup.

    Phases 2 and 3 must be guarded with ``while_match`` (not bare
    ``click:``) so they fire only when the button is visible on the
    list view — ``_tap_region`` for these regions taps the bbox center
    unconditionally, which would mis-fire while still inside a mail
    item if it ran inside the ``while_match: mail_gift`` body.
    """
    assert isinstance(block, list) and len(block) == 3, (
        f"{context}: claim block must have 3 steps (gift loop + claim_all + delete_all), got {len(block) if isinstance(block, list) else type(block).__name__}"
    )
    assert _is_claim_block_root(block[0]), f"{context}: step 0 must be the mail_gift while_match"

    claim_guard = block[1]
    assert claim_guard.get("while_match") == "mail.claim_all", (
        f"{context}: step 1 must guard on mail.claim_all"
    )
    assert claim_guard.get("max") == 1, f"{context}: claim_all guard must cap at max: 1"
    claim_body = claim_guard.get("steps") or []
    assert any(s.get("click") == "mail.claim_all" for s in claim_body if isinstance(s, dict)), (
        f"{context}: claim_all guard must click mail.claim_all"
    )

    delete_guard = block[2]
    assert delete_guard.get("while_match") == "mail.delete_all", (
        f"{context}: step 2 must guard on mail.delete_all"
    )
    assert delete_guard.get("max") == 1, f"{context}: delete_all guard must cap at max: 1"
    delete_body = delete_guard.get("steps") or []
    assert any(s.get("click") == "mail.delete_all" for s in delete_body if isinstance(s, dict)), (
        f"{context}: delete_all guard must click mail.delete_all"
    )


def test_scenario_node_and_priority() -> None:
    doc = _load()
    assert doc["node"] == "mail"
    assert doc["priority"] == 80_000
    assert doc["enabled"] is True


def test_six_top_level_steps_active_plus_five_tabs() -> None:
    doc = _load()
    steps = doc["steps"]
    assert isinstance(steps, list)
    assert len(steps) == 6, f"expected 6 top-level steps (1 active + 5 tabs), got {len(steps)}"


def test_step0_is_bare_active_tab_claim_block() -> None:
    """Step 0 is a bare ``steps:`` group inlining the active-tab claim block.

    No guards (no ``isTabActive`` / ``isRedDot`` / ``cond`` / action key) —
    the active tab has its red-dot cleared, so we always probe for gifts
    directly via the inlined ``while_match: mail_gift``.
    """
    step = _load()["steps"][0]
    assert isinstance(step, dict)
    # Bare-group: only ``steps`` key, nothing else.
    assert set(step.keys()) == {"steps"}, f"step 0 must be a bare group, got keys {list(step.keys())}"
    _assert_claim_block_shape(step["steps"], context="step 0")


def test_each_tab_branch_has_layered_guards() -> None:
    """Steps 1-5: one per tab, with ``isTabActive: false`` outer + ``isRedDot: true`` inner."""
    steps = _load()["steps"]
    for branch_idx, tab_name in enumerate(EXPECTED_TABS, start=1):
        branch = steps[branch_idx]
        assert isinstance(branch, dict), f"branch {branch_idx} not a dict"

        # Outer guard: while_match (NOT bare match — that would abort the
        # scenario on first failure instead of skipping to the next tab).
        assert branch.get("while_match") == tab_name, (
            f"branch {branch_idx} ({tab_name}): outer guard must use while_match on the tab"
        )
        assert branch.get("isTabActive") is False, (
            f"branch {branch_idx} ({tab_name}): outer guard must be isTabActive: false"
        )
        assert branch.get("max") == 1, (
            f"branch {branch_idx} ({tab_name}): outer while_match must cap at max: 1"
        )

        inner = branch.get("steps")
        assert isinstance(inner, list) and len(inner) == 1, (
            f"branch {branch_idx} ({tab_name}): outer guard wraps exactly one inner block"
        )

        # Inner guard: red-dot check on the same tab.
        red = inner[0]
        assert red.get("while_match") == tab_name
        assert red.get("isRedDot") is True
        assert red.get("max") == 1

        body = red.get("steps")
        assert isinstance(body, list) and len(body) >= 3, (
            f"branch {branch_idx} ({tab_name}): inner body must have at least click + wait + claim-block"
        )
        # First two body steps: click the tab + wait.
        assert body[0].get("click") == tab_name
        assert "wait" in body[1]
        # Last body step: bare ``steps:`` group inlining the shared claim block.
        claim_group = body[-1]
        assert set(claim_group.keys()) == {"steps"}, (
            f"branch {branch_idx} ({tab_name}): final body step must be a bare group"
        )
        _assert_claim_block_shape(claim_group["steps"], context=f"branch {branch_idx} ({tab_name})")


def test_claim_block_is_shared_via_yaml_anchor() -> None:
    """All six claim-block usages must reference the same list object — the
    anchor (``&claim_block``) keeps the file DRY. Independent copies would
    invite drift between the active-tab path and the five branch paths.
    """
    steps = _load()["steps"]
    usages = [steps[0]["steps"]]
    for i in range(1, 6):
        body = steps[i]["steps"][0]["steps"]
        usages.append(body[-1]["steps"])
    first = usages[0]
    for other in usages[1:]:
        assert other is first, "claim blocks must share the same list (YAML anchor &claim_block)"
