"""Gift code redeemer via Century Game API (no UI navigation required).

Flow per player per code:
  1. POST /api/player  — login / verify fid
  2. POST /api/captcha — get CAPTCHA image
  3. Solve CAPTCHA with ddddocr
  4. POST /api/gift_code — redeem
  5. Retry up to 3× on CAPTCHA errors (40101 / 40103)
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from century.api import CenturyAPIError, CenturyClient, ErrCode
from century.captcha import solve_captcha
from config.devices import load_devices
from modules.gift_codes.models import GiftCodeDB, RedeemStatus, gift_db_to_yaml_dict

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

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
# FAILED in ``db/giftCodes.yaml`` — sticky pollution from what was just a clean exit.
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
    def __init__(self, codes_path: Path, devices_path: Path) -> None:
        self._codes_path = codes_path
        self._devices_path = devices_path
        self._client = CenturyClient()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def redeem_all(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        summary = GiftRedeemSummary()
        db = self._load_codes()
        registry = load_devices(self._devices_path)
        all_player_ids = registry.all_player_ids()

        total_work = sum(
            1
            for code in db.codes
            if not code.is_effectively_expired()
            for pid in all_player_ids
            if code.needs_redemption(pid)
        )
        done = 0
        if progress_cb is not None:
            progress_cb(0, total_work, "starting")

        for code in db.codes:
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
                    gamer = registry.get_gamer(player_id)
                    nick = gamer.nickname if gamer else player_id
                    progress_cb(done, total_work, f"{code.name} → {nick}")
                if api_ec is not None:
                    code.last_api_err_code = api_ec
                    code.last_api_msg = api_msg if api_msg else None

                if status in (RedeemStatus.CDK_EXPIRED, RedeemStatus.CDK_NOT_FOUND):
                    for pid in all_player_ids:
                        code.user_for[pid] = status
                        gamer = registry.get_gamer(pid)
                        summary.add(
                            GiftRedeemResult(
                                code=code.name,
                                player_id=pid,
                                nickname=gamer.nickname if gamer else pid,
                                status=status,
                                attempted=(pid == player_id),
                                api_err_code=api_ec,
                                api_msg=api_msg,
                            )
                        )
                else:
                    code.user_for[player_id] = status
                    gamer = registry.get_gamer(player_id)
                    summary.add(
                        GiftRedeemResult(
                            code=code.name,
                            player_id=player_id,
                            nickname=gamer.nickname if gamer else player_id,
                            status=status,
                            attempted=True,
                            api_err_code=api_ec,
                            api_msg=api_msg,
                        )
                    )
                self._save_codes(db)

                gamer = registry.get_gamer(player_id)
                nick = gamer.nickname if gamer else player_id
                logger.info("%s (%s): %s", nick, player_id, status.value)

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

    # ------------------------------------------------------------------
    # YAML I/O
    # ------------------------------------------------------------------

    def _load_codes(self) -> GiftCodeDB:
        raw = yaml.safe_load(self._codes_path.read_text()) or {}
        db = GiftCodeDB.model_validate(raw)
        for code in db.codes:
            code.user_for = {
                str(k): (
                    RedeemStatus(v)
                    if v in RedeemStatus._value2member_map_
                    else RedeemStatus.PENDING
                )
                for k, v in code.user_for.items()
            }
        return db

    def _save_codes(self, db: GiftCodeDB) -> None:
        self._codes_path.write_text(
            yaml.dump(gift_db_to_yaml_dict(db), allow_unicode=True, sort_keys=False)
        )


# ------------------------------------------------------------------
# Convenience function used by cmd/gift_code.py
# ------------------------------------------------------------------

async def run_gift_code_redeemer(
    codes_path: Path,
    devices_path: Path,
    bot_instance_map: dict[str, str] | None = None,  # unused, kept for compat
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    redeemer = GiftCodeRedeemer(codes_path, devices_path)
    return await redeemer.redeem_all(progress_cb=progress_cb)
