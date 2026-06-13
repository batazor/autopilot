"""Phase 0 — `src/config/games.py` helper smoke tests."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.games import (
    DEFAULT_GAME,
    GAMES,
    GAMES_DIR_NAME,
    GameSpec,
    default_game,
    game_for_package,
    game_ids_for_packages,
    games_root,
    is_known_game,
    is_module_reference,
    iter_games,
    matching_packages_for_game,
    modules_root_for,
    package_for_game,
    packages_for_game,
    spec_for_game,
    split_repo_relative,
)
from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


def test_default_game_is_wos() -> None:
    assert default_game() == "wos"
    assert DEFAULT_GAME == "wos"


def test_iter_games_lists_registry_in_order() -> None:
    assert iter_games() == ("wos", "kingshot")


def test_games_root_resolves_under_repo() -> None:
    assert games_root() == repo_root() / GAMES_DIR_NAME


def test_games_root_honours_explicit_repo_root(tmp_path: Path) -> None:
    assert games_root(repo_root=tmp_path) == tmp_path.resolve() / "games"


def test_modules_root_for_returns_games_root(tmp_path: Path) -> None:
    """Phase 3: each game's modules live under games/<game>."""
    assert modules_root_for("wos", repo_root=tmp_path) == tmp_path.resolve() / "games" / "wos"
    assert (
        modules_root_for("kingshot", repo_root=tmp_path) == tmp_path.resolve() / "games" / "kingshot"
    )


def test_modules_root_for_default_repo_matches_repo_root() -> None:
    assert modules_root_for("wos") == repo_root() / "games" / "wos"


def test_is_module_reference_accepts_repo_relative_module_paths() -> None:
    assert is_module_reference("games/wos/core/heroes/references/page.png")
    assert is_module_reference("games/wos/ads/references/promo.png")
    assert is_module_reference("games/kingshot/core/main_city/area.yaml")
    assert is_module_reference("games\\wos\\core\\heroes\\area.yaml")  # backslashes


def test_is_module_reference_rejects_non_module_paths() -> None:
    assert not is_module_reference("")
    assert not is_module_reference("references/x.png")  # root references
    legacy = "modules" + "/core/heroes/x.png"  # explicit legacy prefix
    assert not is_module_reference(legacy)
    assert not is_module_reference("games")  # no game
    assert not is_module_reference("games/wos")  # no module
    assert not is_module_reference("games/brawl_stars/x/foo")  # unknown game
    assert not is_module_reference("../etc/passwd")
    assert not is_module_reference("games/wos/../etc")


def test_split_repo_relative_extracts_module_id_and_tail() -> None:
    assert split_repo_relative("games/wos/core/heroes/references/p.png") == (
        "core/heroes",
        "references/p.png",
    )
    assert split_repo_relative("games/wos/ads/references/promo.png") == (
        "ads",
        "references/promo.png",
    )
    assert split_repo_relative("games/wos/core/heroes/area.yaml") == (
        "core/heroes",
        "area.yaml",
    )
    assert split_repo_relative("games/wos/events/trials/scenarios/x.yaml") == (
        "events/trials",
        "scenarios/x.yaml",
    )


def test_split_repo_relative_handles_edge_cases() -> None:
    assert split_repo_relative("") is None
    assert split_repo_relative("references/x.png") is None  # not under games/
    assert split_repo_relative("games") is None  # no game
    assert split_repo_relative("games/wos") is None  # no module id
    assert split_repo_relative("games/wos/references/x.png") is None  # missing module id
    # Plain module id with no internal-dir marker:
    assert split_repo_relative("games/wos/ads") == ("ads", "")
    assert split_repo_relative("games/wos/core/heroes") == ("core/heroes", "")


def test_split_game_module_includes_game_segment() -> None:
    from config.games import split_game_module

    assert split_game_module("games/wos/core/heroes/references/p.png") == (
        "wos",
        "core/heroes",
        "references/p.png",
    )
    assert split_game_module("games/kingshot/core/main_city/area.yaml") == (
        "kingshot",
        "core/main_city",
        "area.yaml",
    )
    assert split_game_module("games/brawl_stars/x/area.yaml") is None  # unknown game


def test_modules_path_prefix_returns_two_segment_prefix() -> None:
    from config.games import modules_path_prefix

    assert modules_path_prefix() == "games/wos"
    assert modules_path_prefix("wos") == "games/wos"
    assert modules_path_prefix("kingshot") == "games/kingshot"


# --- registry ---------------------------------------------------------------


def test_registry_includes_wos_and_kingshot() -> None:
    assert set(GAMES) == {"wos", "kingshot"}
    assert isinstance(GAMES["wos"], GameSpec)
    assert GAMES["wos"].package == "com.gof.global"
    assert GAMES["wos"].package_aliases == ("com.xyz.gof",)
    assert GAMES["kingshot"].package == "com.run.tower.defense"
    assert GAMES["kingshot"].package_aliases == ("com.abc.defense",)


def test_spec_for_game_and_package_lookups() -> None:
    assert spec_for_game("wos").id == "wos"
    assert package_for_game("wos") == "com.gof.global"
    assert packages_for_game("wos") == ("com.gof.global", "com.xyz.gof")
    assert package_for_game("kingshot") == "com.run.tower.defense"
    assert packages_for_game("kingshot") == (
        "com.run.tower.defense",
        "com.abc.defense",
    )


def test_spec_for_game_raises_on_unknown() -> None:
    with pytest.raises(KeyError, match="unknown game id"):
        spec_for_game("brawl_stars")


def test_game_for_package_round_trip() -> None:
    for game in iter_games():
        pkg = package_for_game(game)
        assert game_for_package(pkg) == game


def test_game_for_package_accepts_wos_beta_package() -> None:
    assert game_for_package("com.xyz.gof") == "wos"


def test_game_for_package_accepts_kingshot_beta_package() -> None:
    assert game_for_package("com.abc.defense") == "kingshot"


def test_package_set_helpers_accept_wos_beta_package() -> None:
    installed = {"com.android.systemui", "com.xyz.gof"}
    assert game_ids_for_packages(installed) == ["wos"]
    assert matching_packages_for_game("wos", installed) == ("com.xyz.gof",)


def test_package_set_helpers_accept_kingshot_beta_package() -> None:
    installed = {"com.android.systemui", "com.abc.defense"}
    assert game_ids_for_packages(installed) == ["kingshot"]
    assert matching_packages_for_game("kingshot", installed) == ("com.abc.defense",)


def test_game_for_package_returns_none_for_unknown_package() -> None:
    assert game_for_package("") is None
    assert game_for_package("com.unrelated.app") is None


def test_is_known_game_matches_registry() -> None:
    assert is_known_game("wos")
    assert is_known_game("kingshot")
    assert not is_known_game("")
    assert not is_known_game("brawl_stars")
