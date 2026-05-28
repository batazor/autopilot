from __future__ import annotations

from pathlib import Path

from config.games import default_game, modules_root_for
from config.module_discovery import iter_module_dirs
from dsl import template_resolver

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_chief_profile_alias_paths_point_to_who_i_am_sources() -> None:
    alias = REPO_ROOT / "games/wos/core/chief_profile"
    live = REPO_ROOT / "games/wos/core/who_i_am"

    expected = {
        alias / "area.yaml": live / "area.yaml",
        alias / "references": live / "references",
        alias / "screen_verify.yaml": live / "routes/screen_verify.yaml",
        alias / "routes/edge_taps.yaml": live / "routes/edge_taps.yaml",
        alias / "routes/screen_verify.yaml": live / "routes/screen_verify.yaml",
        alias / "scenarios/by_cron/sync_chief_profile.yaml": live
        / "scenarios/who_i_am.yaml",
    }
    for alias_path, live_path in expected.items():
        assert alias_path.is_symlink(), f"{alias_path} should be a symlink"
        assert alias_path.resolve() == live_path.resolve()


def test_chief_profile_alias_is_not_a_discovered_module() -> None:
    modules = set(iter_module_dirs(REPO_ROOT, game=default_game()))
    modules_root = modules_root_for(default_game(), repo_root=REPO_ROOT)

    assert modules_root / "core/who_i_am" in modules
    assert modules_root / "core/chief_profile" not in modules

    resolved = template_resolver.resolve(REPO_ROOT, "who_i_am")
    assert resolved is not None
    assert resolved.path.resolve() == (
        modules_root / "core/who_i_am/scenarios/who_i_am.yaml"
    ).resolve()
