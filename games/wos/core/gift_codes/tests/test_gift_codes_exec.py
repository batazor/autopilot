"""Tests for the in-game gift-code redeem exec (build-aware code-set routing).

The exec types codes into the game client only on builds the public API rejects
(beta, RU). The build → gift-code-game routing and the canonical-build skip are
the load-bearing new behavior; the tap-by-tap navigation is left to live runs.
"""
from __future__ import annotations

import pytest
from games.wos.core.gift_codes import exec as gc_exec

from tasks.dsl_exec import DslExecContext


def _ctx() -> DslExecContext:
    return DslExecContext(redis_client=None, player_id="p1", instance_id="bs1")


# ── pure routing ────────────────────────────────────────────────────────────


def test_in_game_games_are_the_wos_overlay_catalogs() -> None:
    assert {"wos_beta", "wos_ru"} == gc_exec._IN_GAME_GIFT_CODE_GAMES


@pytest.mark.parametrize(
    ("catalog", "expected"),
    [
        ("wos_ru", "wos_ru"),
        ("wos_beta", "wos_beta"),
        ("wos", None),       # canonical → API redemption, not in-game
        ("", None),
        (None, None),
        ("kingshot", None),
    ],
)
def test_in_game_gift_code_game(catalog: str | None, expected: str | None) -> None:
    assert gc_exec._in_game_gift_code_game(catalog) == expected


# ── exec routing (no device taps) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_exec_skips_canonical_build(mocker) -> None:
    mocker.patch("services.get_active_module_catalog", return_value="wos")
    # Navigation must never start on a canonical build.
    bot_actions = mocker.patch("tasks.dsl_runtime.bot_actions", side_effect=AssertionError)

    ctx = _ctx()
    await gc_exec._exec_redeem_in_game_gift_codes(ctx)

    assert ctx.result == {"action": "skipped_build"}
    bot_actions.assert_not_called()


@pytest.mark.asyncio
async def test_exec_reads_codes_for_the_running_builds_game(mocker) -> None:
    mocker.patch("services.get_active_module_catalog", return_value="wos_ru")
    list_codes = mocker.patch("config.giftcodes_db.list_codes", return_value=[])
    # Nothing pending → returns before any navigation.
    bot_actions = mocker.patch("tasks.dsl_runtime.bot_actions", side_effect=AssertionError)

    ctx = _ctx()
    await gc_exec._exec_redeem_in_game_gift_codes(ctx)

    list_codes.assert_called_once_with(game="wos_ru")
    assert ctx.result == {"action": "none_pending", "game": "wos_ru"}
    bot_actions.assert_not_called()
