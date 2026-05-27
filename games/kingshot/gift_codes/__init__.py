"""Kingshot gift-code module — independent from the WOS module.

This module mirrors :mod:`games.wos.gift_codes` but is intentionally **not**
parameterized — Kingshot's API differs enough (no captcha, ms timestamps,
different err_code semantics, aggregator-based discovery) that a separate
codebase is clearer than threading ``game`` through every WOS callsite.

Shared concerns live elsewhere:

- ``RedeemStatus`` / ``GiftCode`` (Pydantic models) are imported from
  :mod:`games.wos.gift_codes.models` — they're pure data types whose schema
  covers both games (``VIP_LEVEL_TOO_LOW`` was added for Kingshot).
- The SQLite persistence in :mod:`config.giftcodes_db` is game-scoped via
  ``game="kingshot"`` keyword arguments.
- The HTTP client :class:`century.api.CenturyClient` accepts a
  :class:`century.games.GameConfig`; we pass :data:`century.games.KINGSHOT`.
"""

from games.kingshot.gift_codes.redeemer import (
    GiftCodeRedeemer,
    GiftRedeemSummary,
    run_gift_code_redeemer,
)
from games.kingshot.gift_codes.scraper import poll_once, run_scraper_loop
from games.wos.gift_codes.models import GiftCode, GiftCodeDB, RedeemStatus

__all__ = [
    "GiftCode",
    "GiftCodeDB",
    "GiftCodeRedeemer",
    "GiftRedeemSummary",
    "RedeemStatus",
    "poll_once",
    "run_gift_code_redeemer",
    "run_scraper_loop",
]
