"""``load_merged_analyze_yaml`` includes module overlay rules.

Without this wiring, moving overlay rules into ``modules/<id>/analyze/analyze.yaml``
would silently disable them — :func:`analysis.overlay.run_overlay_analysis``
must merge every module manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from analysis.overlay_manifest import load_merged_analyze_yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rule_names(doc: dict[str, Any]) -> list[str]:
    return [str(r.get("name") or "") for r in doc.get("overlay") or [] if isinstance(r, dict)]


def test_reconnect_module_overlay_rule_present_in_merged_manifest() -> None:
    doc = load_merged_analyze_yaml(REPO_ROOT)
    names = _rule_names(doc)
    assert "reconnect_button.visible" in names


def test_mail_module_overlay_rules_present_in_merged_manifest() -> None:
    """All six mail-screen rules live in the mail module and must surface here."""
    doc = load_merged_analyze_yaml(REPO_ROOT)
    names = _rule_names(doc)

    for expected in (
        "mail_gift.visible",
        "mail.tab.wars.has_red_dot",
        "mail.tab.alliance.has_red_dot",
        "mail.tab.system.has_red_dot",
        "mail.tab.reports.has_red_dot",
        "mail.tab.starred.has_red_dot",
    ):
        assert expected in names, f"missing rule {expected!r} in merged overlay"


def test_merged_loader_picks_up_arbitrary_module_overlay(tmp_path: Path) -> None:
    """Synthetic repo: empty core + one module with a single rule → it surfaces."""
    module_dir = tmp_path / "modules" / "fake"
    (module_dir / "analyze").mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: fake\ntitle: Fake\n", encoding="utf-8")
    (module_dir / "analyze" / "analyze.yaml").write_text(
        yaml.safe_dump(
            {
                "overlay": [
                    {
                        "name": "fake.visible",
                        "region": "fake.region",
                        "action": "findIcon",
                        "screens": ["fake"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    doc = load_merged_analyze_yaml(tmp_path)
    assert _rule_names(doc) == ["fake.visible"]


def test_module_overlay_rules_appended_after_core(tmp_path: Path) -> None:
    """Multiple module manifests merge in discovery order."""
    module_dir = tmp_path / "modules" / "core" / "mod_a"
    (module_dir / "analyze").mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: mod_a\n", encoding="utf-8")
    (module_dir / "analyze" / "analyze.yaml").write_text(
        yaml.safe_dump({"overlay": [{"name": "mod_a.rule", "region": "x", "screens": ["x"]}]}),
        encoding="utf-8",
    )

    module_dir_b = tmp_path / "modules" / "core" / "mod_b"
    (module_dir_b / "analyze").mkdir(parents=True)
    (module_dir_b / "module.yaml").write_text("id: mod_b\n", encoding="utf-8")
    (module_dir_b / "analyze" / "analyze.yaml").write_text(
        yaml.safe_dump(
            {"overlay": [{"name": "core.rule", "region": "core.region", "screens": ["x"]}]}
        ),
        encoding="utf-8",
    )

    doc = load_merged_analyze_yaml(tmp_path)
    assert _rule_names(doc) == ["mod_a.rule", "core.rule"]
