from __future__ import annotations

from pathlib import Path

import pytest

from config.startup_validation import assert_startup_configs_valid, validate_startup_configs


def _write_edge_taps(root: Path, text: str = "edges: {}\n") -> None:
    (root / "navigation").mkdir()
    (root / "navigation" / "edge_taps.yaml").write_text(text, encoding="utf-8")


def test_startup_validation_reports_missing_analyze_scenario(tmp_path: Path) -> None:
    (tmp_path / "analyze" / "analyze_pages").mkdir(parents=True)
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"claim_all","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        "include:\n  - analyze_pages/common.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze_pages" / "common.yaml").write_text(
        """
overlay:
  - name: claim_all.visible
    region: claim_all
    action: findIcon
    pushScenario:
      - name: missing_claim_scenario
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:claim_all.visible"
    assert "missing_claim_scenario" in issues[0].message


def test_startup_validation_fails_fast(tmp_path: Path) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        """
overlay:
  - name: broken.visible
    region: missing_region
    action: findIcon
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="startup config validation failed: 1 issue"):
        assert_startup_configs_valid(tmp_path)


def test_startup_validation_reports_missing_edge_tap_region(tmp_path: Path) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(
        tmp_path,
        """
edges:
  main_city:
    mail: [missing_mail_button]
""".lstrip(),
    )
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"mail.new","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "edge_taps:main_city->mail"
    assert "missing_mail_button" in issues[0].message
