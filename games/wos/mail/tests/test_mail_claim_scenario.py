"""Structural checks for tab-specific mail claim scenarios."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay import run_overlay_analysis_sync
from analysis.overlay_area import default_area_doc_for_overlay
from dsl import template_resolver

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
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


def test_mail_tab_strip_red_dot_pushes_tab_claim_scenario() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "mail_page.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        area_doc=default_area_doc_for_overlay(REPO_ROOT),
        current_screen="mail.system",
    )

    row = out.get("mail.tabs.visible_red_dot")
    assert isinstance(row, dict), out
    assert row["matched"] is True
    assert row["active_page_id"] == "mail.claim.system"
    assert row["red_dot_pages"] == ["mail.claim.starred"]
    assert row["pushScenario"] == [
        {
            "type": "mail.claim.starred",
            "priority": None,
            "ttl": None,
            "dsl_scenario": None,
        }
    ]
