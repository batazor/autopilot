"""WOS gift-code scraper + Century API redeemer.

Two responsibilities, intentionally co-located so the scheduler can drive
``poll_once`` and ``run_gift_code_redeemer`` as one logical poller per game:

- :func:`poll_once` — scrape https://www.wosrewards.com/ for new codes and
  upsert them into the SQLite ``gift_codes`` table (game-scoped to ``wos``).
- :func:`run_gift_code_redeemer` — for every code that still needs work,
  log each known account in and submit it via the Century Game API. Steps
  per (player × code): ``/api/player``, ``/api/captcha`` (ddddocr solve),
  ``/api/gift_code``, with up to 3 captcha retries.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from century.api import CenturyAPIError, CenturyClient, ErrCode
from century.captcha import solve_captcha
from century.gift_codes.models import RedeemStatus
from config.devices import load_devices
from config.giftcodes_db import (
    code_exists,
    list_codes,
    list_external_gamers,
    set_redemption,
    set_redemption_bulk,
    upsert_code,
)
from licensing.gate import has_feature

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_GAME_ID = "wos"
_EXTERNAL_ACCOUNTS_FEATURE = "gift_codes.external_accounts"

# ── Scraper ────────────────────────────────────────────────────────────────

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


# ── Redeemer ───────────────────────────────────────────────────────────────

_MAX_CAPTCHA_RETRIES = 3
_INTER_PLAYER_DELAY = 2.0   # seconds between players
_INTER_CODE_DELAY = 10.0    # seconds between codes (avoids captcha frequency errors)
_CAPTCHA_RETRY_DELAY = 8.0  # base delay before re-requesting captcha after error
_JITTER = 0.25              # ±25% multiplicative jitter on every sleep below


def _jittered(seconds: float) -> float:
    """Spread bot calls so multiple instances on one IP don't sync up."""
    if seconds <= 0:
        return 0.0
    return seconds * (1.0 + random.uniform(-_JITTER, _JITTER))


def _captcha_backoff(attempt: int) -> float:
    """Exponential backoff for captcha retries: 8s → 16s → 32s, with jitter."""
    return _jittered(_CAPTCHA_RETRY_DELAY * (2 ** (attempt - 1)))


def _is_too_frequent_error(exc: BaseException) -> bool:
    return "too frequent" in str(exc).lower()


# Errors that mean "the interpreter is going down, give up cleanly". When asyncio's
# default executor is shut down (Ctrl+C) while a coroutine is mid-``getaddrinfo`` /
# ``connect_tcp``, the network call surfaces as a normal ``RuntimeError``. Without
# special-casing it, the broad ``except Exception`` arms below mark the code as
# FAILED in the gift_codes table — sticky pollution from what was just a clean exit.
_SHUTDOWN_ERROR_MARKERS = (
    "cannot schedule new futures after shutdown",
    "event loop is closed",
)


def _is_shutdown_error(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _SHUTDOWN_ERROR_MARKERS)


@dataclass(frozen=True)
class GiftRedeemResult:
    code: str
    player_id: str
    nickname: str
    status: RedeemStatus
    attempted: bool = True
    api_err_code: int | None = None
    api_msg: str | None = None

    def to_dict(self) -> dict[str, object]:
        row: dict[str, object] = {
            "code": self.code,
            "player_id": self.player_id,
            "nickname": self.nickname,
            "status": self.status.value,
            "attempted": self.attempted,
        }
        if self.api_err_code is not None:
            row["api_err_code"] = self.api_err_code
        if self.api_msg:
            row["api_msg"] = self.api_msg
        return row


@dataclass
class GiftRedeemSummary:
    results: list[GiftRedeemResult] = field(default_factory=list)

    def add(self, result: GiftRedeemResult) -> None:
        self.results.append(result)

    def counts_by_status(self) -> dict[str, int]:
        counts = Counter(r.status.value for r in self.results)
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, object]:
        return {
            "total": len(self.results),
            "counts": self.counts_by_status(),
            "results": [r.to_dict() for r in self.results],
        }


def _ec_to_status(ec: ErrCode) -> RedeemStatus:
    match ec:
        case ErrCode.SUCCESS:
            return RedeemStatus.SUCCESS
        case ErrCode.ALREADY_RECEIVED_1 | ErrCode.ALREADY_RECEIVED_2 | ErrCode.ALREADY_RECEIVED_3:
            return RedeemStatus.ALREADY_RECEIVED
        case ErrCode.CDK_EXPIRED:
            return RedeemStatus.CDK_EXPIRED
        case ErrCode.CDK_NOT_FOUND:
            return RedeemStatus.CDK_NOT_FOUND
        case ErrCode.STOVE_LEVEL_TOO_LOW:
            return RedeemStatus.STOVE_LEVEL_TOO_LOW
        case _:
            return RedeemStatus.FAILED


class GiftCodeRedeemer:
    def __init__(self) -> None:
        self._client = CenturyClient()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def redeem_all(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        summary = GiftRedeemSummary()
        codes = list_codes()
        registry = load_devices()
        local_player_ids = registry.all_player_ids()
        all_player_ids = list(local_player_ids)
        external_nicks: dict[str, str] = {}
        if has_feature(_EXTERNAL_ACCOUNTS_FEATURE):
            for ext in list_external_gamers(game=_GAME_ID, enabled_only=True):
                pid = str(ext.player_id)
                if pid in local_player_ids:
                    continue
                all_player_ids.append(pid)
                external_nicks[pid] = ext.nickname or pid
            if external_nicks:
                logger.info(
                    "WOS redeem: including %d external account(s)", len(external_nicks)
                )
        else:
            logger.debug(
                "WOS redeem: %r feature not licensed — external accounts skipped",
                _EXTERNAL_ACCOUNTS_FEATURE,
            )

        def _nick(pid: str) -> str:
            if pid in external_nicks:
                return external_nicks[pid]
            gamer = registry.get_gamer(pid)
            return gamer.nickname if gamer else pid

        total_work = sum(
            1
            for code in codes
            if not code.is_effectively_expired()
            for pid in all_player_ids
            if code.needs_redemption(pid)
        )
        done = 0
        if progress_cb is not None:
            progress_cb(0, total_work, "starting")

        for code in codes:
            if code.is_effectively_expired():
                logger.info("Skipping expired or API-dead code: %s", code.name)
                continue

            needs_any = any(code.needs_redemption(pid) for pid in all_player_ids)
            if not needs_any:
                continue

            logger.info("=== Code: %s ===", code.name)
            stop = False

            for player_id in all_player_ids:
                if not code.needs_redemption(player_id):
                    continue

                status, api_ec, api_msg = await self._redeem_one(int(player_id), code.name)
                done += 1
                if progress_cb is not None:
                    progress_cb(done, total_work, f"{code.name} → {_nick(player_id)}")
                if api_ec is not None:
                    code.last_api_err_code = api_ec
                    code.last_api_msg = api_msg or None
                    upsert_code(code.name, last_api_err_code=api_ec, last_api_msg=api_msg or "")

                if status in (RedeemStatus.CDK_EXPIRED, RedeemStatus.CDK_NOT_FOUND):
                    # Code is globally dead — stamp every known player with the
                    # terminal status so future runs skip the whole code instantly.
                    set_redemption_bulk(code.name, all_player_ids, status)
                    for pid in all_player_ids:
                        code.user_for[pid] = status
                        summary.add(
                            GiftRedeemResult(
                                code=code.name,
                                player_id=pid,
                                nickname=_nick(pid),
                                status=status,
                                attempted=(pid == player_id),
                                api_err_code=api_ec,
                                api_msg=api_msg,
                            )
                        )
                else:
                    set_redemption(code.name, player_id, status)
                    code.user_for[player_id] = status
                    summary.add(
                        GiftRedeemResult(
                            code=code.name,
                            player_id=player_id,
                            nickname=_nick(player_id),
                            status=status,
                            attempted=True,
                            api_err_code=api_ec,
                            api_msg=api_msg,
                        )
                    )

                logger.info("%s (%s): %s", _nick(player_id), player_id, status.value)

                if status == RedeemStatus.CDK_NOT_FOUND:
                    logger.warning("Code %s does not exist — stopping", code.name)
                    stop = True
                    break

                await asyncio.sleep(_jittered(_INTER_PLAYER_DELAY))

            if stop:
                break
            await asyncio.sleep(_jittered(_INTER_CODE_DELAY))
        return summary

    # ------------------------------------------------------------------
    # Single player + code
    # ------------------------------------------------------------------

    async def _redeem_one(self, fid: int, code: str) -> tuple[RedeemStatus, int | None, str | None]:
        # Step 1: login
        try:
            await self._client.fetch_player(fid)
        except Exception as exc:
            if _is_shutdown_error(exc):
                raise
            logger.exception("Login failed for fid=%d", fid)
            return RedeemStatus.FAILED, None, None

        # Steps 2-3-4: captcha + redeem, up to 3 attempts
        for attempt in range(1, _MAX_CAPTCHA_RETRIES + 1):
            try:
                captcha_data = await self._client.fetch_captcha(fid)
            except CenturyAPIError as exc:
                if _is_too_frequent_error(exc) and attempt < _MAX_CAPTCHA_RETRIES:
                    delay = _captcha_backoff(attempt)
                    logger.warning(
                        "Captcha fetch rate-limited fid=%d attempt=%d/%d, sleeping %.1fs",
                        fid, attempt, _MAX_CAPTCHA_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.exception("Captcha request failed fid=%d attempt=%d", fid, attempt)
                return RedeemStatus.FAILED, None, None
            except Exception as exc:
                if _is_shutdown_error(exc):
                    raise
                logger.exception("Captcha request failed fid=%d attempt=%d", fid, attempt)
                return RedeemStatus.FAILED, None, None

            try:
                captcha_text = solve_captcha(captcha_data.img_b64)
            except Exception:
                logger.exception("Captcha solve failed fid=%d", fid)
                return RedeemStatus.FAILED, None, None

            try:
                ec, api_msg = await self._client.redeem(fid, code, captcha_text)
            except Exception as exc:
                if _is_shutdown_error(exc):
                    raise
                logger.exception("Redeem call failed fid=%d", fid)
                return RedeemStatus.FAILED, None, None

            if ec in (ErrCode.CAPTCHA_TOO_FREQUENT, ErrCode.CAPTCHA_ERROR):
                if attempt < _MAX_CAPTCHA_RETRIES:
                    delay = _captcha_backoff(attempt)
                    logger.debug(
                        "Captcha error ec=%s, retry %d/%d after %.1fs",
                        ec, attempt, _MAX_CAPTCHA_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                return RedeemStatus.FAILED, None, None

            return _ec_to_status(ec), ec.value, api_msg

        return RedeemStatus.FAILED, None, None


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,  # unused, kept for compat
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    redeemer = GiftCodeRedeemer()
    return await redeemer.redeem_all(progress_cb=progress_cb)
