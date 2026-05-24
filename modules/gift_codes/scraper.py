"""Scraper for https://www.wosrewards.com/ — auto-discovers new gift codes.

New codes are upserted into the SQLite ``gift_codes`` table; this module no
longer touches ``db/giftCodes.yaml``.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from config.giftcodes_db import code_exists, upsert_code

logger = logging.getLogger(__name__)

_REWARDS_URL = "https://www.wosrewards.com/"
_POLL_INTERVAL_SECONDS = 3600  # check every hour


async def fetch_codes_from_web() -> list[str]:
    """Scrape wosrewards.com and return all gift code strings found.

    Raises:
        httpx.RequestError: network/TLS/timeout problems.
        httpx.HTTPStatusError: non-2xx response.
    """
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(_REWARDS_URL)
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    codes: list[str] = []
    for tag in soup.find_all("h5", class_="font-bold"):
        text = tag.get_text(strip=True)
        if text:
            codes.append(text)
    return codes


def add_new_codes(found: list[str]) -> list[str]:
    """Insert any genuinely new codes into SQLite. Returns the names added."""
    added: list[str] = []
    for code in found:
        if not code_exists(code):
            upsert_code(code)
            added.append(code)
            logger.info("New code discovered: %s", code)
    return added


async def poll_once() -> list[str]:
    """One scrape cycle. Returns newly added codes (empty list if none).

    Network/HTTP problems are logged as warnings (transient, expected).
    Unexpected errors (parsing, DB, validation) are logged with traceback.
    """
    try:
        found = await fetch_codes_from_web()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "wosrewards returned HTTP %s: %s", e.response.status_code, e.request.url
        )
        return []
    except httpx.RequestError as e:
        logger.warning("wosrewards unreachable: %s: %s", type(e).__name__, e)
        return []
    except Exception:
        logger.exception("wosrewards fetch failed")
        return []

    logger.debug("wosrewards: found %d codes on page", len(found))
    try:
        return add_new_codes(found)
    except Exception:
        logger.exception("failed to persist new gift codes to SQLite")
        return []


async def run_scraper_loop(
    on_new_codes: object | None = None,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> None:
    """Continuously poll wosrewards.com and optionally call on_new_codes(list[str])."""
    logger.info("WOS rewards scraper started, interval=%ds", interval)
    while True:
        try:
            new = await poll_once()
            if new and on_new_codes is not None:
                try:
                    await on_new_codes(new)  # type: ignore[operator]  # ty: ignore[call-non-callable]
                except Exception:
                    logger.exception("on_new_codes callback failed for %s", new)
        except asyncio.CancelledError:
            logger.info("WOS rewards scraper cancelled")
            raise
        except Exception:
            logger.exception("scraper iteration crashed; continuing after interval")
        await asyncio.sleep(interval)
