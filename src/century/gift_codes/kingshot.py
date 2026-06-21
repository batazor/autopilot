"""Kingshot gift-code aggregator scraper + Century API redeemer.

Two responsibilities co-located so the scheduler can drive ``poll_once``
and ``run_gift_code_redeemer`` as one logical poller per game:

- :func:`poll_once` — fetch new codes from the Kingshot aggregator
  (``ks-gift-code-api.whiteout-bot.com``) and upsert into the SQLite
  ``gift_codes`` table scoped to ``game="kingshot"``.
- :func:`run_gift_code_redeemer` — for every code that still needs work,
  log each known account in (``/api/player``) and submit (``/api/gift_code``
  with ms timestamp). No captcha step (Kingshot's API skips it).

Differences from the WOS module (:mod:`century.gift_codes.wos`):
- No captcha — no ddddocr, no captcha backoff.
- New ``40004 TIMEOUT_RETRY`` err_code → internal retry; only surfaces as
  ``FAILED`` after the budget is exhausted.
- ``40017`` / ``40018`` → terminal-per-player :attr:`RedeemStatus.VIP_LEVEL_TOO_LOW`.
- Tighter inter-call cadence (no captcha pressure).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from century.api import CenturyAPIError, CenturyClient, ErrCode
from century.games import KINGSHOT
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

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_GAME_ID = KINGSHOT.id

# Redeeming codes against accounts the bot doesn't own (alliance members,
# partner farms, etc.) is handled by this redeemer.

# ── Scraper ────────────────────────────────────────────────────────────────

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
    informational and real expiry is driven by API err_codes.
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


# ── Redeemer ───────────────────────────────────────────────────────────────

# No captcha pressure → tighter cadence is safe. The aggregator + redeem
# endpoint will push back with 40004 (TIMEOUT_RETRY) or HTTP 429 if we
# over-pace; both paths are handled below.
_INTER_PLAYER_DELAY = 1.0
_INTER_CODE_DELAY = 4.0
_JITTER = 0.25

# Internal retry for transient 40004 TIMEOUT_RETRY errors. Exposed as FAILED
# only after the budget is exhausted — callers don't see TIMEOUT_RETRY as a
# RedeemStatus value.
_MAX_TIMEOUT_RETRIES = 3
_TIMEOUT_RETRY_BASE_DELAY = 2.0  # seconds, doubled per attempt


def _jittered(seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return seconds * (1.0 + random.uniform(-_JITTER, _JITTER))


def _timeout_backoff(attempt: int) -> float:
    """Exponential backoff for 40004 retries: 2s → 4s → 8s, with jitter."""
    return _jittered(_TIMEOUT_RETRY_BASE_DELAY * (2 ** (attempt - 1)))


# Shutdown markers — when the asyncio loop closes (Ctrl+C) network calls
# can surface as plain RuntimeError. Without special-casing this, the broad
# ``except Exception`` arms below would mark the code as FAILED in the DB,
# leaving sticky pollution from what was actually a clean exit.
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
        case (
            ErrCode.ALREADY_RECEIVED_1
            | ErrCode.ALREADY_RECEIVED_2
            | ErrCode.ALREADY_RECEIVED_3
        ):
            return RedeemStatus.ALREADY_RECEIVED
        case ErrCode.CDK_EXPIRED:
            return RedeemStatus.CDK_EXPIRED
        case ErrCode.CDK_NOT_FOUND:
            return RedeemStatus.CDK_NOT_FOUND
        case ErrCode.STOVE_LEVEL_TOO_LOW:
            return RedeemStatus.STOVE_LEVEL_TOO_LOW
        case ErrCode.RECHARGE_MONEY | ErrCode.RECHARGE_MONEY_VIP:
            return RedeemStatus.VIP_LEVEL_TOO_LOW
        case _:
            return RedeemStatus.FAILED


class GiftCodeRedeemer:
    def __init__(self) -> None:
        self._client = CenturyClient(game=KINGSHOT)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def redeem_all(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        summary = GiftRedeemSummary()
        codes = list_codes(game=_GAME_ID)
        registry = load_devices()
        # Filter to gamers whose device profile is Kingshot. Profiles without
        # an explicit game fall back to the device default (see
        # ``DeviceEntry.game_for_profile``), so single-game registries still
        # resolve correctly.
        local_player_ids = registry.all_player_ids(game=_GAME_ID)
        all_player_ids = list(local_player_ids)
        external_nicks: dict[str, str] = {}
        for ext in list_external_gamers(game=_GAME_ID, enabled_only=True):
            pid = str(ext.player_id)
            if pid in local_player_ids:
                continue  # local row wins — skip duplicate
            all_player_ids.append(pid)
            external_nicks[pid] = ext.nickname or pid
        if external_nicks:
            logger.info(
                "Kingshot redeem: including %d external account(s)",
                len(external_nicks),
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
                logger.info("Skipping expired or API-dead Kingshot code: %s", code.name)
                continue

            needs_any = any(code.needs_redemption(pid) for pid in all_player_ids)
            if not needs_any:
                continue

            logger.info("=== Kingshot code: %s ===", code.name)
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
                    upsert_code(
                        code.name,
                        game=_GAME_ID,
                        last_api_err_code=api_ec,
                        last_api_msg=api_msg or "",
                    )

                if status in (RedeemStatus.CDK_EXPIRED, RedeemStatus.CDK_NOT_FOUND):
                    # Globally dead — stamp every known KS player so future
                    # runs skip the code instantly.
                    set_redemption_bulk(code.name, all_player_ids, status, game=_GAME_ID)
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
                    set_redemption(code.name, player_id, status, game=_GAME_ID)
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

                logger.info("Kingshot %s (%s): %s", _nick(player_id), player_id, status.value)

                if status == RedeemStatus.CDK_NOT_FOUND:
                    logger.warning("Kingshot code %s does not exist — stopping", code.name)
                    stop = True
                    break

                await asyncio.sleep(_jittered(_INTER_PLAYER_DELAY))

            if stop:
                break
            await asyncio.sleep(_jittered(_INTER_CODE_DELAY))
        return summary

    # ------------------------------------------------------------------
    # Single account (all its pending codes)
    # ------------------------------------------------------------------

    async def redeem_for_player(
        self,
        fid: str | int,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        """Redeem every currently-needed Kingshot code for a single account."""
        pid = str(fid)
        summary = GiftRedeemSummary()
        codes = list_codes(game=_GAME_ID)

        nick = pid
        for ext in list_external_gamers(game=_GAME_ID):
            if str(ext.player_id) == pid:
                nick = ext.nickname or pid
                break

        pending = [
            code
            for code in codes
            if not code.is_effectively_expired() and code.needs_redemption(pid)
        ]
        total_work = len(pending)
        if progress_cb is not None:
            progress_cb(0, total_work, "starting")

        for done, code in enumerate(pending, start=1):
            status, api_ec, api_msg = await self._redeem_one(int(pid), code.name)
            if progress_cb is not None:
                progress_cb(done, total_work, f"{code.name} → {nick}")
            if api_ec is not None:
                code.last_api_err_code = api_ec
                code.last_api_msg = api_msg or None
                upsert_code(
                    code.name,
                    game=_GAME_ID,
                    last_api_err_code=api_ec,
                    last_api_msg=api_msg or "",
                )
            set_redemption(code.name, pid, status, game=_GAME_ID)
            code.user_for[pid] = status
            summary.add(
                GiftRedeemResult(
                    code=code.name,
                    player_id=pid,
                    nickname=nick,
                    status=status,
                    attempted=True,
                    api_err_code=api_ec,
                    api_msg=api_msg,
                )
            )
            logger.info("Kingshot %s (%s): %s", nick, pid, status.value)
            if status == RedeemStatus.CDK_NOT_FOUND:
                logger.warning("Kingshot code %s does not exist — stopping", code.name)
                break
            await asyncio.sleep(_jittered(_INTER_CODE_DELAY))
        return summary

    # ------------------------------------------------------------------
    # Single player + code
    # ------------------------------------------------------------------

    async def _redeem_one(
        self, fid: int, code: str
    ) -> tuple[RedeemStatus, int | None, str | None]:
        # Step 1: login (no captcha for Kingshot).
        try:
            await self._client.fetch_player(fid)
        except Exception as exc:
            if _is_shutdown_error(exc):
                raise
            logger.exception("Kingshot login failed for fid=%d", fid)
            return RedeemStatus.FAILED, None, None

        # Step 2: redeem, retry internally on TIMEOUT_RETRY (40004).
        for attempt in range(1, _MAX_TIMEOUT_RETRIES + 1):
            try:
                ec, api_msg = await self._client.redeem(fid, code)
            except CenturyAPIError:
                logger.exception("Kingshot redeem call failed fid=%d attempt=%d", fid, attempt)
                return RedeemStatus.FAILED, None, None
            except Exception as exc:
                if _is_shutdown_error(exc):
                    raise
                logger.exception("Kingshot redeem call failed fid=%d attempt=%d", fid, attempt)
                return RedeemStatus.FAILED, None, None

            if ec == ErrCode.TIMEOUT_RETRY and attempt < _MAX_TIMEOUT_RETRIES:
                delay = _timeout_backoff(attempt)
                logger.warning(
                    "Kingshot redeem fid=%d code=%s got TIMEOUT_RETRY (attempt %d/%d), sleeping %.1fs",
                    fid, code, attempt, _MAX_TIMEOUT_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue

            return _ec_to_status(ec), ec.value, api_msg

        # Retry budget exhausted on TIMEOUT_RETRY.
        logger.warning("Kingshot redeem fid=%d code=%s exhausted TIMEOUT_RETRY budget", fid, code)
        return RedeemStatus.FAILED, ErrCode.TIMEOUT_RETRY.value, "TIMEOUT RETRY"


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,  # unused, kept for parity with WOS
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    _ = bot_instance_map
    redeemer = GiftCodeRedeemer()
    return await redeemer.redeem_all(progress_cb=progress_cb)


async def run_gift_code_redeemer_for_player(
    fid: str | int,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    """Redeem all pending Kingshot codes for a single account."""
    redeemer = GiftCodeRedeemer()
    return await redeemer.redeem_for_player(fid, progress_cb=progress_cb)
