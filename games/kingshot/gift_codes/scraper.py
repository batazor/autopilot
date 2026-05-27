"""Kingshot gift-code aggregator scraper.

Polls the ``ks-gift-code-api.whiteout-bot.com`` aggregator (configured in
:data:`century.games.KINGSHOT.aggregator_url`) for new codes. Response shape::

    {"codes": ["CODE1 DD.MM.YYYY", "CODE2 DD.MM.YYYY", ...]}

The date is ``discovered_at`` — **not** an expiration. We store it on the
:class:`GiftCode` only for visibility; expiry is driven by API ``err_code``
(``CDK_EXPIRED`` / ``CDK_NOT_FOUND``) as in WOS.

This module deliberately does *not* import the WOS scraper — that one parses
``wosrewards.com`` HTML, which doesn't exist for Kingshot. The two scrapers
share zero code on purpose.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

import httpx

from century.games import KINGSHOT
from config.giftcodes_db import code_exists, upsert_code

logger = logging.getLogger(__name__)

_GAME_ID = KINGSHOT.id
_AGGREGATOR_URL = KINGSHOT.aggregator_url
_AGGREGATOR_KEY = KINGSHOT.aggregator_api_key
_POLL_INTERVAL_SECONDS = 3600  # check every hour
_CODE_RE = re.compile(r"^[a-zA-Z0-9]+$")
_DATE_FMT = "%d.%m.%Y"


async def fetch_codes_from_aggregator() -> list[tuple[str, datetime | None]]:
    """Fetch ``[(code, discovered_at), ...]`` from the Kingshot aggregator.

    The date column is optional — malformed dates are tolerated (the bot
    upstream sometimes drops them), but malformed code strings are dropped.

    Raises:
        httpx.RequestError: network/TLS/timeout problems.
        httpx.HTTPStatusError: non-2xx response.
    """
    headers = {"X-API-Key": _AGGREGATOR_KEY}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(_AGGREGATOR_URL, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    out: list[tuple[str, datetime | None]] = []
    for raw in body.get("codes") or []:
        parts = str(raw).strip().split()
        if not parts:
            continue
        code = parts[0]
        if not _CODE_RE.fullmatch(code):
            continue
        discovered_at: datetime | None = None
        if len(parts) >= 2:
            try:
                discovered_at = datetime.strptime(parts[1], _DATE_FMT).replace(tzinfo=UTC)
            except ValueError:
                discovered_at = None
        out.append((code, discovered_at))
    return out


def add_new_codes(found: list[tuple[str, datetime | None]]) -> list[str]:
    """Insert any genuinely new codes into SQLite. Returns the names added.

    ``discovered_at`` is stored as ``expires`` on the upsert call — same
    semantic as WOS's wosrewards.com pipeline, where the date column is
    informational and real expiry is driven by API err_codes. Per-game DB
    scoping keeps WOS rows untouched.
    """
    added: list[str] = []
    for code, discovered_at in found:
        if code_exists(code, game=_GAME_ID):
            continue
        upsert_code(code, game=_GAME_ID, expires=discovered_at)
        added.append(code)
        logger.info("New Kingshot code discovered: %s", code)
    return added


async def poll_once() -> list[str]:
    """One scrape cycle. Returns newly added codes (empty list if none)."""
    try:
        found = await fetch_codes_from_aggregator()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Kingshot aggregator returned HTTP %s: %s",
            e.response.status_code, e.request.url,
        )
        return []
    except httpx.RequestError as e:
        logger.warning("Kingshot aggregator unreachable: %s: %s", type(e).__name__, e)
        return []
    except Exception:
        logger.exception("Kingshot aggregator fetch failed")
        return []

    logger.debug("Kingshot aggregator: %d codes in response", len(found))
    try:
        return add_new_codes(found)
    except Exception:
        logger.exception("failed to persist new Kingshot gift codes to SQLite")
        return []


async def run_scraper_loop(
    on_new_codes: object | None = None,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> None:
    """Continuously poll the Kingshot aggregator and optionally call ``on_new_codes(list[str])``."""
    logger.info("Kingshot gift-code scraper started, interval=%ds", interval)
    while True:
        try:
            new = await poll_once()
            if new and on_new_codes is not None:
                try:
                    await on_new_codes(new)  # type: ignore[operator]  # ty: ignore[call-non-callable]
                except Exception:
                    logger.exception("on_new_codes callback failed for %s", new)
        except asyncio.CancelledError:
            logger.info("Kingshot gift-code scraper cancelled")
            raise
        except Exception:
            logger.exception("scraper iteration crashed; continuing after interval")
        await asyncio.sleep(interval)
