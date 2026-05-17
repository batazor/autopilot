"""Scraper for https://www.wosrewards.com/ — auto-discovers new gift codes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
import yaml
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from modules.gift_codes.models import GiftCode, GiftCodeDB, gift_db_to_yaml_dict

if TYPE_CHECKING:
    from pathlib import Path

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


def _load_codes(path: Path) -> GiftCodeDB:
    if not path.exists():
        return GiftCodeDB()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        logger.error("gift codes YAML is malformed at %s: %s", path, e)
        raise
    return GiftCodeDB.model_validate(raw)


def _save_codes(path: Path, db: GiftCodeDB) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.dump(gift_db_to_yaml_dict(db), allow_unicode=True, sort_keys=False))
    tmp.replace(path)


def add_new_codes(path: Path, found: list[str]) -> list[str]:
    """Add any genuinely new codes to the YAML. Returns list of added codes."""
    db = _load_codes(path)
    existing = {c.name.upper() for c in db.codes}
    added: list[str] = []
    for code in found:
        if code.upper() not in existing:
            db.codes.append(GiftCode(name=code))
            existing.add(code.upper())
            added.append(code)
            logger.info("New code discovered: %s", code)
    if added:
        _save_codes(path, db)
    return added


async def poll_once(codes_path: Path) -> list[str]:
    """One scrape cycle. Returns newly added codes (empty list if none).

    Network/HTTP problems are logged as warnings (transient, expected).
    Unexpected errors (parsing, disk, validation) are logged with traceback.
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
        return add_new_codes(codes_path, found)
    except (OSError, yaml.YAMLError):
        logger.exception("failed to persist new gift codes to %s", codes_path)
        return []
    except Exception:
        logger.exception("unexpected error while adding new codes")
        return []


async def run_scraper_loop(
    codes_path: Path,
    on_new_codes: object | None = None,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> None:
    """Continuously poll wosrewards.com and optionally call on_new_codes(list[str])."""
    logger.info("WOS rewards scraper started, interval=%ds", interval)
    while True:
        try:
            new = await poll_once(codes_path)
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
