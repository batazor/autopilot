"""Bulk enable/disable helpers on the scenarios config page."""

from __future__ import annotations

from pathlib import Path

import yaml

from ui.views.scenarios import (
    _apply_bulk_enabled_to_ids,
    _format_bulk_result_message,
    _scenario_ids_from_meta,
    _set_scenario_enabled,
)


def test_scenario_ids_from_meta() -> None:
    meta = [
        (Path("a.yaml"), "x/a.yaml", "one", "One", {}),
        (Path("b.yaml"), "y/b.yaml", "two", "Two", {}),
    ]
    assert _scenario_ids_from_meta(meta) == {"one", "two"}


def test_apply_bulk_enabled_writes_only_selected(tmp_path: Path) -> None:
    scenarios_dir = tmp_path
    p1 = scenarios_dir / "one.yaml"
    p2 = scenarios_dir / "two.yaml"
    p1.write_text(yaml.dump({"id": "one", "enabled": False}, sort_keys=False))
    p2.write_text(yaml.dump({"id": "two", "enabled": False}, sort_keys=False))
    path_by_id = {"one": p1, "two": p2}
    result = _apply_bulk_enabled_to_ids(
        selected_ids={"one"},
        path_by_id=path_by_id,
        repo_root=tmp_path,
        enabled=True,
    )
    assert result.changed == ("one.yaml",)
    assert result.unchanged == ()
    assert yaml.safe_load(p1.read_text())["enabled"] is True
    assert yaml.safe_load(p2.read_text())["enabled"] is False


def test_apply_bulk_enabled_skips_already_set(tmp_path: Path) -> None:
    scenarios_dir = tmp_path
    path = scenarios_dir / "s.yaml"
    path.write_text(yaml.dump({"id": "s", "enabled": True}, sort_keys=False))
    result = _apply_bulk_enabled_to_ids(
        selected_ids={"s"},
        path_by_id={"s": path},
        repo_root=tmp_path,
        enabled=True,
    )
    assert result.changed == ()
    assert result.unchanged == ("s.yaml",)


def test_format_bulk_result_message() -> None:
    from ui.views.scenarios import _BulkEnableResult

    msg = _format_bulk_result_message(
        _BulkEnableResult(changed=("a.yaml",), unchanged=("b.yaml",), missing=()),
        enabled=True,
    )
    assert "a.yaml" in msg
    assert "b.yaml" in msg


def test_set_scenario_enabled_preserves_other_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.dump({"id": "s", "name": "S", "enabled": True, "steps": []}, sort_keys=False)
    )
    _set_scenario_enabled(path, False)
    raw = yaml.safe_load(path.read_text())
    assert raw["enabled"] is False
    assert raw["name"] == "S"
    assert raw["steps"] == []
