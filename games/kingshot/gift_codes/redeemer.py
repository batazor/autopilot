"""Kingshot gift-code redeemer.

Flow per player per code (no captcha, unlike WOS):

  1. POST /api/player    — login / verify fid
  2. POST /api/gift_code — redeem (with ms timestamp)
  3. Translate err_code → :class:`RedeemStatus`

Differences from the WOS redeemer (``games.wos.gift_codes.redeemer``):

- No captcha step → no ddddocr, no 8 s captcha backoff.
- New ``40004 TIMEOUT_RETRY`` err_code → internal retry with exponential
  backoff; surfaces as ``FAILED`` only after the retry budget is exhausted.
- ``40017 RECHARGE_MONEY`` / ``40018 RECHARGE_MONEY_VIP`` → terminal-per-player
  status :attr:`RedeemStatus.VIP_LEVEL_TOO_LOW` (mirrors the WOS handling of
  ``STOVE_LEVEL_TOO_LOW``).
- Lower throttle defaults — without captcha pressure 1 s / 4 s between players
  and codes is safe; WOS keeps the older 2 s / 10 s values.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from games.wos.gift_codes.models import RedeemStatus

from century.api import CenturyAPIError, CenturyClient, ErrCode
from century.games import KINGSHOT
from config.devices import load_devices
from config.giftcodes_db import (
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

_GAME_ID = KINGSHOT.id

# Pro feature flag for redeeming codes against accounts the bot doesn't own
# (alliance members, partner farms, etc.). Gated at three layers: this
# redeemer (here), the API (rejects POST/DELETE on the table), and the UI.
# Belt-and-suspenders — a stale DB row from a downgraded license still
# stops being processed because the feature check is on every redeem run.
_EXTERNAL_ACCOUNTS_FEATURE = "gift_codes.external_accounts"

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
        # Filter to gamers whose device profile is Kingshot. The registry
        # exposes ``all_player_ids(game=...)`` after Phase 2 of the master
        # migration; legacy registries without per-profile game fall back
        # to returning every gamer.
        local_player_ids = registry.all_player_ids(game=_GAME_ID)
        all_player_ids = list(local_player_ids)
        external_nicks: dict[str, str] = {}
        if has_feature(_EXTERNAL_ACCOUNTS_FEATURE):
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
        else:
            logger.debug(
                "Kingshot redeem: %r feature not licensed — external accounts skipped",
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


# ------------------------------------------------------------------
# Convenience entry point (matches the WOS module's signature)
# ------------------------------------------------------------------


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,  # unused, kept for parity with WOS
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    _ = bot_instance_map
    redeemer = GiftCodeRedeemer()
    return await redeemer.redeem_all(progress_cb=progress_cb)
