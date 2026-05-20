"""Debug scenarios picker lists core and module scenario YAMLs."""

from __future__ import annotations

from pathlib import Path

from dsl.registry import scenario_source_label

REPO = Path(__file__).resolve().parents[2]
_MAIL_SCENARIO = REPO / "modules" / "mail" / "scenarios" / "mail.claim.{tab}.yaml"


def test_scenario_source_label_for_module_path() -> None:
    assert (
        scenario_source_label(_MAIL_SCENARIO, REPO)
        == "modules/mail/scenarios/mail.claim.{tab}.yaml"
    )


def test_list_scenario_files_includes_module_mail_scenario() -> None:
    # Import lazily — ``ui.views.debug_scenarios`` runs Streamlit page code at
    # module import that requires a non-empty scenario tree; importing it when
    # the tree is empty (e.g. mid-refactor) crashes the collector.
    from ui.views.debug_scenarios import _list_scenario_files

    files = _list_scenario_files(REPO, "all")
    mail = next((sf for sf in files if sf.key == "mail.claim.system"), None)
    assert mail is not None
    assert mail.rel_scenarios == "modules/mail/scenarios/mail.claim.{tab}.yaml"
    assert mail.repo_rel == "modules/mail/scenarios/mail.claim.{tab}.yaml"
