"""Per-game configuration for the Century Game gift-code API.

Both Whiteout Survival and Kingshot run on the same Century Games platform and
share the gift-code protocol (POST /api/player, POST /api/gift_code, MD5 sign).
What differs between games:

  - host (``base_url`` + ``redemption_url`` for origin/referer)
  - MD5 ``salt``
  - whether ``/api/captcha`` exists (WOS yes, KS no)
  - time unit for the ``/api/gift_code`` payload (WOS seconds, KS milliseconds)
  - the whiteout-bot.com aggregator shard used to discover new codes

``GameConfig`` carries all of these. ``CenturyClient`` takes one as input;
``GAMES["wos"]`` / ``GAMES["kingshot"]`` are the canonical instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GameConfig:
    id: str
    name: str
    base_url: str
    redemption_url: str
    salt: str
    has_captcha: bool
    redeem_time_unit: Literal["s", "ms"]
    aggregator_url: str
    aggregator_api_key: str


# X-API-Key value shared across all whiteout-project / kingshot-project bot
# installs. Same string on both WOS and KS shards — they treat it as a soft
# rate-limit token, not real auth.
_AGGREGATOR_KEY = "super_secret_bot_token_nobody_will_ever_find"


WOS = GameConfig(
    id="wos",
    name="Whiteout Survival",
    base_url="https://wos-giftcode-api.centurygame.com/api",
    redemption_url="https://wos-giftcode.centurygame.com",
    salt="tB87#kPtkxqOS2",
    has_captcha=True,
    redeem_time_unit="s",
    aggregator_url="http://gift-code-api.whiteout-bot.com/giftcode_api.php",
    aggregator_api_key=_AGGREGATOR_KEY,
)


KINGSHOT = GameConfig(
    id="kingshot",
    name="Kingshot",
    base_url="https://kingshot-giftcode.centurygame.com/api",
    redemption_url="https://kingshot-giftcode.centurygame.com",
    salt="mN4!pQs6JrYwV9",
    has_captcha=False,
    redeem_time_unit="ms",
    aggregator_url="http://ks-gift-code-api.whiteout-bot.com/giftcode_api.php",
    aggregator_api_key=_AGGREGATOR_KEY,
)


GAMES: dict[str, GameConfig] = {WOS.id: WOS, KINGSHOT.id: KINGSHOT}


def get_game(game_id: str) -> GameConfig:
    """Return the GameConfig for *game_id*. Raises ValueError on unknown IDs."""
    try:
        return GAMES[game_id]
    except KeyError as exc:
        known = ", ".join(sorted(GAMES))
        msg = f"unknown game {game_id!r}; known: {known}"
        raise ValueError(msg) from exc
