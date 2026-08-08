"""Overlay module catalogs must be validated, not just the base games.

`games/wos/ru/**` ships real `area.yaml` and `analyze.yaml` that a live RU build
executes. Startup validation iterated `iter_games()` — games only — and the
analyze path did not even accept a catalog, so it resolved the process-active
one, which at supervisor boot is unbound and falls back to `wos`. The RU rules
were therefore validated by nothing on the build that actually runs them.
"""

from __future__ import annotations

import pytest

from analysis.overlay_manifest import load_merged_analyze_yaml
from config.games import iter_games, iter_module_catalogs
from config.paths import repo_root


def test_iter_games_stays_games_only() -> None:
    """An overlay is not a game: it has no GameSpec, and `spec_for_game` raises
    for it. Widening `iter_games` would break `adb.controller_process` and
    `config.research`, which iterate it expecting real games."""
    assert iter_games() == ("wos", "kingshot")


def test_iter_module_catalogs_includes_the_overlays() -> None:
    catalogs = iter_module_catalogs()

    assert set(iter_games()).issubset(catalogs)
    assert "wos_ru" in catalogs


def test_absent_overlay_tree_is_skipped(tmp_path) -> None:
    """A fixture repo without the overlay dirs must not gain phantom catalogs."""
    assert iter_module_catalogs(tmp_path) == iter_games()


def test_ru_overlay_rules_are_visible_under_their_catalog() -> None:
    """The concrete hole: a live RU-only rule reachable by no validation pass."""
    root = repo_root()

    base = load_merged_analyze_yaml(root, game="wos").get("overlay") or []
    ru = load_merged_analyze_yaml(root, game="wos_ru").get("overlay") or []

    def _ru_rule_names(rules: list) -> set[str]:
        return {
            str(r.get("name"))
            for r in rules
            if isinstance(r, dict) and str(r.get("name", "")).endswith(".ru")
        }

    assert not _ru_rule_names(base), "base catalog must not carry RU overlay rules"
    assert _ru_rule_names(ru), "RU catalog must expose its own overlay rules"


@pytest.mark.parametrize("catalog", ["wos", "wos_ru"])
def test_merged_analyze_is_cached_per_catalog(catalog: str) -> None:
    """`game` is part of the cache key. Reading it from process state inside the
    cached body would make the key a lie — the same bug we fixed in the scenario
    tree scan."""
    root = repo_root()

    first = load_merged_analyze_yaml(root, game=catalog).get("overlay") or []
    second = load_merged_analyze_yaml(root, game=catalog).get("overlay") or []
    other = load_merged_analyze_yaml(
        root, game="wos_ru" if catalog == "wos" else "wos"
    ).get("overlay") or []

    assert len(first) == len(second)
    assert len(first) != len(other), "catalogs must not share a cache entry"


def test_real_repo_still_boots_clean() -> None:
    """Widening coverage must not introduce a boot-blocking error."""
    from config.startup_validation import validate_startup_configs

    assert [i for i in validate_startup_configs() if i.severity == "error"] == []
