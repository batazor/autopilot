"""The validator must resolve screen names the way the runtime does.

Two independent defects lived in ``_collect_screen_verify_entries``:

* it was FIRST-wins while the runtime loader (``navigation.screen_graph``) is
  LAST-wins, and its docstring asserted the opposite of the truth. Under the
  ``wos_ru`` catalog the overlay redefines ``chat`` / ``mail`` /
  ``welcome_back`` / ``survivor_status``, so the worker ran the overlay entries
  while the validator checked the base ones;
* it pooled every catalog into one namespace, so a name defined by two games
  (``welcome_back`` exists in both wos and kingshot) was silently handed to
  whichever game sorted last — a resolution no runtime performs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from config.startup_validation import _collect_screen_verify_entries_for_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _write_module(root: Path, rel: str, screens: dict[str, Any]) -> Path:
    mod = root / "games" / "wos" / rel
    (mod / "routes").mkdir(parents=True)
    (mod / "module.yaml").write_text(f"id: {rel.replace('/', '_')}\n", encoding="utf-8")
    path = mod / "routes" / "screen_verify.yaml"
    path.write_text(yaml.dump({"screens": screens}), encoding="utf-8")
    return path


def test_last_declaration_wins(mocker: Any, tmp_path: Path) -> None:
    """Two modules declaring the same screen: the later one in discovery order
    is the one the runtime uses, so it must be the one the validator checks."""
    first = _write_module(tmp_path, "aaa_first", {"dupe": {"priority": 30}})
    second = _write_module(
        tmp_path, "zzz_second", {"dupe": {"priority": 55, "terminal": True}}
    )
    mocker.patch(
        "config.startup_validation._screen_verify_yaml_paths_for_catalog",
        return_value=[first, second],
    )

    entries = _collect_screen_verify_entries_for_catalog(tmp_path, "wos")

    prio, terminal, src = entries["dupe"]
    assert (prio, terminal) == (55, True), "first-wins would have given (30, False)"
    assert src.endswith("zzz_second/routes/screen_verify.yaml")


def test_entries_are_scoped_to_one_catalog(mocker: Any, tmp_path: Path) -> None:
    """A catalog only sees its own declarations — no cross-catalog bleed."""
    wos = _write_module(tmp_path, "wos_only", {"shared": {"priority": 30}})
    other = _write_module(tmp_path, "other_only", {"shared": {"priority": 99}})

    def _paths(_root: Path, catalog: str) -> list[Path]:
        return [wos] if catalog == "wos" else [other]

    mocker.patch(
        "config.startup_validation._screen_verify_yaml_paths_for_catalog",
        side_effect=_paths,
    )

    assert _collect_screen_verify_entries_for_catalog(tmp_path, "wos")["shared"][0] == 30
    assert (
        _collect_screen_verify_entries_for_catalog(tmp_path, "kingshot")["shared"][0] == 99
    )


def test_real_repo_boots_clean() -> None:
    """The precedence fix must not introduce a boot-blocking error.

    Findings it surfaces are warnings by agreement — the backlog gets annotated
    before this is promoted.
    """
    from config.startup_validation import validate_startup_configs

    issues = validate_startup_configs()

    assert [i for i in issues if i.severity == "error"] == []


def test_findings_are_deduplicated() -> None:
    """Catalogs share ~99% of the tree; the same defect in a shared file must be
    reported once, not once per catalog."""
    from config.startup_validation import validate_startup_configs

    issues = validate_startup_configs()
    keys = [(i.severity, i.source, i.message) for i in issues]

    assert len(keys) == len(set(keys))
