"""Kingshot beta gift-code source.

Beta codes are discovered from a Discord channel and stored under
``game="kingshot_beta"``. Redemption is intentionally a no-op until the beta
redeem endpoint/protocol is known.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from century.gift_codes.discord_source import (
    NullGiftRedeemSummary,
    poll_discord_channel_once,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_GAME_ID = "kingshot_beta"
_CHANNEL_ENV = "KINGSHOT_BETA_GIFT_CODES_DISCORD_CHANNEL_ID"


async def poll_once() -> list[str]:
    return await poll_discord_channel_once(game=_GAME_ID, channel_env=_CHANNEL_ENV)


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> NullGiftRedeemSummary:
    del bot_instance_map
    if progress_cb is not None:
        progress_cb(0, 0, "beta redeem not configured")
    return NullGiftRedeemSummary()
