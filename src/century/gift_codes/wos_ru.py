"""WOS RU («Белая мгла») gift-code source.

The Russian re-skin (``com.gof.globalru``) runs on a separate Century shard that
the global gift-code API doesn't recognise, so codes can neither be scraped from
the global sources nor redeemed over the public API. Instead they are **entered
manually** by the operator (from the RU community) and stored under
``game="wos_ru"``; application happens inside the game client for the player
logged in on the RU device (see ``games/wos/core/gift_codes/exec.py``).

``poll_once`` is therefore a no-op (there is no automatic source) and
``run_gift_code_redeemer`` returns an empty summary (redemption is in-game only),
mirroring :mod:`century.gift_codes.wos_beta`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from century.gift_codes.discord_source import NullGiftRedeemSummary

if TYPE_CHECKING:
    from collections.abc import Callable

_GAME_ID = "wos_ru"


async def poll_once() -> list[str]:
    # No automatic source — RU codes are added by hand via the dashboard.
    return []


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> NullGiftRedeemSummary:
    del bot_instance_map
    if progress_cb is not None:
        progress_cb(0, 0, "RU codes apply in game for the current player")
    return NullGiftRedeemSummary()
