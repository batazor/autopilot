from __future__ import annotations

import pytest

from api.services.game_resolver import resolve_game


def test_resolve_game_accepts_module_catalog() -> None:
    assert resolve_game(game="wos_beta") == "wos_beta"


def test_resolve_game_rejects_unknown_catalog() -> None:
    with pytest.raises(ValueError, match="unknown game/module catalog"):
        resolve_game(game="brawl_stars")
