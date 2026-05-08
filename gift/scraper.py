"""Scraper for https://www.wosrewards.com/ — auto-discovers new gift codes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from gift.models import GiftCode, GiftCodeDB, gift_db_to_yaml_dict

logger = logging.getLogger(__name__)

_REWARDS_URL = "https://www.wosrewards.com/"
_POLL_INTERVAL_SECONDS = 3600  # check every hour


async def fetch_codes_from_web() -> list[str]:
    """Scrape wosrewards.com and return all gift code strings found."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(_REWARDS_URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    codes: list[str] = []
    for tag in soup.find_all("h5", class_="font-bold"):
        text = tag.get_text(strip=True)
        if text:
            codes.append(text)
    return codes


def _load_codes(path: Path) -> GiftCodeDB:
    if not path.exists():
        return GiftCodeDB()
    raw = yaml.safe_load(path.read_text()) or {}
    return GiftCodeDB.model_validate(raw)


def _save_codes(path: Path, db: GiftCodeDB) -> None:
    path.write_text(yaml.dump(gift_db_to_yaml_dict(db), allow_unicode=True, sort_keys=False))


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
    """One scrape cycle. Returns newly added codes (empty list if none)."""
    try:
        found = await fetch_codes_from_web()
        logger.debug("wosrewards: found %d codes on page", len(found))
        return add_new_codes(codes_path, found)
    except Exception:
        logger.exception("wosrewards scrape failed")
        return []


async def run_scraper_loop(
    codes_path: Path,
    on_new_codes: object | None = None,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> None:
    """Continuously poll wosrewards.com and optionally call on_new_codes(list[str])."""
    logger.info("WOS rewards scraper started, interval=%ds", interval)
    while True:
        new = await poll_once(codes_path)
        if new and on_new_codes is not None:
            await on_new_codes(new)  # type: ignore[operator]
        await asyncio.sleep(interval)
