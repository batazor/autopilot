"""Debug scenarios picker lists core and module scenario YAMLs."""

from __future__ import annotations

from pathlib import Path

import pytest

from scenarios.registry import scenario_source_label

REPO = Path(__file__).resolve().parents[1]
_MAIL_SCENARIO = REPO / "modules" / "mail" / "scenarios" / "read_mail_gifts.yaml"


def test_scenario_source_label_for_module_path() -> None:
    if not _MAIL_SCENARIO.is_file():
        pytest.skip("modules/mail/scenarios/read_mail_gifts.yaml missing — module not in tree")
    assert (
        scenario_source_label(_MAIL_SCENARIO, REPO)
        == "modules/mail/scenarios/read_mail_gifts.yaml"
    )


def test_list_scenario_files_includes_module_mail_scenario() -> None:
    if not _MAIL_SCENARIO.is_file():
        pytest.skip("modules/mail/scenarios/read_mail_gifts.yaml missing — module not in tree")
    # Import lazily — ``ui.views.debug_scenarios`` runs Streamlit page code at
    # module import that requires a non-empty scenario tree; importing it when
    # the tree is empty (e.g. mid-refactor) crashes the collector.
    from ui.views.debug_scenarios import _list_scenario_files

    files = _list_scenario_files(REPO, "all")
    mail = next((sf for sf in files if sf.key == "read_mail_gifts"), None)
    assert mail is not None
    assert mail.rel_scenarios == "modules/mail/scenarios/read_mail_gifts.yaml"
    assert mail.repo_rel == "modules/mail/scenarios/read_mail_gifts.yaml"
