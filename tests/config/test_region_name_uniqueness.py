"""Duplicate region names are dead config that still looks live.

Region lookup is first-wins over the merged area doc, so a name declared twice
resolves to whichever copy the merge saw first and every later declaration is
unreachable. `layout.area_regions.validate_unique_region_names` checks this one
screen entry at a time and runs only from the dashboard's save path — never at
load, never at boot.

The check has to be narrow or its warnings become noise: an overlay redeclaring
a base name IS the overlay mechanism, and two games sharing a name never merge.
Of the 61 raw cross-file collisions in the tree, 18 are overlay overrides and 41
are wos-vs-kingshot; only 2 are real.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from config.startup_validation import _validate_region_name_uniqueness

if TYPE_CHECKING:
    from pathlib import Path


def _area(path: Path, screens: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "screens": screens}), encoding="utf-8")


def _screen(ocr: str, *names: str) -> dict[str, Any]:
    return {
        "screen_id": "main_city",
        "ocr": ocr,
        "regions": [{"name": n, "bbox": {"x": 1, "y": 1, "width": 1, "height": 1}} for n in names],
    }


def _messages(issues: list[Any]) -> str:
    return "\n".join(i.message for i in issues)


def test_duplicate_within_one_file_is_reported(tmp_path: Path) -> None:
    """The live case: two screen entries for the same screen, one re-captured,
    both declaring the same region — the later one never resolves."""
    _area(
        tmp_path / "games" / "wos" / "core" / "main_city" / "area.yaml",
        [_screen("references/v1.png", "a.dup"), _screen("references/v3.png", "a.dup")],
    )
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert "'a.dup' is declared 2x" in _messages(issues)
    assert all(i.severity == "warning" for i in issues)


def test_an_overlay_redeclaring_a_base_name_is_not_a_collision(tmp_path: Path) -> None:
    """That is the overlay mechanism, not a mistake."""
    _area(tmp_path / "games" / "wos" / "core" / "intel" / "area.yaml", [_screen("b.png", "intel.title")])
    _area(tmp_path / "games" / "wos" / "ru" / "intel" / "area.yaml", [_screen("r.png", "intel.title")])
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert issues == []


def test_the_same_name_in_two_games_is_not_a_collision(tmp_path: Path) -> None:
    """Their catalogs never merge, so neither shadows the other."""
    _area(tmp_path / "games" / "wos" / "mail" / "area.yaml", [_screen("w.png", "mail.title")])
    _area(tmp_path / "games" / "kingshot" / "mail" / "area.yaml", [_screen("k.png", "mail.title")])
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert issues == []


def test_two_files_of_one_game_sharing_a_name_is_reported(tmp_path: Path) -> None:
    _area(tmp_path / "games" / "wos" / "core" / "common" / "area.yaml", [_screen("c.png", "button.free")])
    _area(tmp_path / "games" / "wos" / "deals" / "hall" / "area.yaml", [_screen("h.png", "button.free")])
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert "declared in 2 files of the same catalog" in _messages(issues)


def test_a_clean_tree_reports_nothing(tmp_path: Path) -> None:
    _area(tmp_path / "games" / "wos" / "core" / "a" / "area.yaml", [_screen("a.png", "one", "two")])
    _area(tmp_path / "games" / "wos" / "core" / "b" / "area.yaml", [_screen("b.png", "three")])
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert issues == []


def test_unreadable_area_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "games" / "wos" / "core" / "broken" / "area.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("{ not: valid", encoding="utf-8")
    issues: list[Any] = []

    _validate_region_name_uniqueness(tmp_path, issues)

    assert issues == []


def test_real_repo_findings_are_warnings_only() -> None:
    """The backlog it surfaces must never block a boot."""
    from config.paths import repo_root

    issues: list[Any] = []
    _validate_region_name_uniqueness(repo_root(), issues)

    assert issues, "the check found nothing at all — it is probably broken"
    assert all(i.severity == "warning" for i in issues)
