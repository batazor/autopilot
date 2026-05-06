"""Century Game API client for Whiteout Survival.

Endpoints:
  POST /api/player      — player info (login step)
  POST /api/captcha     — get CAPTCHA image
  POST /api/gift_code   — redeem gift code

All requests are signed with MD5:
  sign = md5(sorted_params_string + SALT)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from enum import IntEnum

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_API_BASE = "https://wos-giftcode-api.centurygame.com/api"
_SALT = "tB87#kPtkxqOS2"
_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}


class ErrCode(IntEnum):
    SUCCESS = 20000
    ALREADY_RECEIVED_1 = 40008
    ALREADY_RECEIVED_2 = 40011
    CDK_EXPIRED = 40007
    CDK_NOT_FOUND = 40014
    CAPTCHA_TOO_FREQUENT = 40101
    CAPTCHA_ERROR = 40103


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _timestamp_ns() -> str:
    return str(time.time_ns())


def _sign(*pairs: tuple[str, str]) -> str:
    """Build sorted param string and sign with SALT."""
    param_str = "&".join(f"{k}={v}" for k, v in sorted(pairs))
    return _md5(param_str + _SALT)


@dataclass(frozen=True)
class PlayerData:
    fid: int
    nickname: str
    kid: int
    stove_level: int
    avatar_image: str
    stove_lv_content: int


@dataclass(frozen=True)
class CaptchaData:
    img_b64: str  # base64 PNG, may include data: prefix


class CenturyAPIError(Exception):
    pass


class CenturyClient:
    """Async HTTP client for the Century Game gift code API."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Player info
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=4))
    async def fetch_player(self, fid: int) -> PlayerData:
        ts = _timestamp_ns()
        sign = _sign(("fid", str(fid)), ("time", ts))
        data = {"fid": str(fid), "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=_HEADERS, timeout=self._timeout) as client:
            resp = await client.post(f"{_API_BASE}/player", data=data)
            resp.raise_for_status()
            body = resp.json()

        if body.get("msg", "").lower() != "success":
            raise CenturyAPIError(f"player fetch failed: {body.get('msg')} err_code={body.get('err_code')}")

        d = body["data"]
        return PlayerData(
            fid=int(d["fid"]),
            nickname=d["nickname"],
            kid=d["kid"],
            stove_level=d["stove_lv"],
            avatar_image=d.get("avatar_image", ""),
            stove_lv_content=int(d.get("stove_lv_content", 0)),
        )

    # ------------------------------------------------------------------
    # Captcha
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=4))
    async def fetch_captcha(self, fid: int) -> CaptchaData:
        ts = _timestamp_ns()
        sign = _sign(("fid", str(fid)), ("init", "0"), ("time", ts))
        data = {"fid": str(fid), "init": "0", "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=_HEADERS, timeout=self._timeout) as client:
            resp = await client.post(f"{_API_BASE}/captcha", data=data)
            resp.raise_for_status()
            body = resp.json()

        if body.get("msg", "").upper() != "SUCCESS":
            raise CenturyAPIError(f"captcha request failed: {body.get('msg')}")

        return CaptchaData(img_b64=body["data"]["img"])

    # ------------------------------------------------------------------
    # Gift code redemption
    # ------------------------------------------------------------------

    async def redeem(self, fid: int, code: str, captcha_code: str) -> ErrCode:
        ts = _timestamp_ns()
        sign = _sign(
            ("captcha_code", captcha_code),
            ("cdk", code),
            ("fid", str(fid)),
            ("time", ts),
        )
        data = {
            "fid": str(fid),
            "cdk": code,
            "captcha_code": captcha_code,
            "time": ts,
            "sign": sign,
        }

        async with httpx.AsyncClient(headers=_HEADERS, timeout=self._timeout) as client:
            resp = await client.post(f"{_API_BASE}/gift_code", data=data)
            resp.raise_for_status()
            body = resp.json()

        ec = body.get("err_code", -1)
        logger.debug("redeem fid=%d code=%s ec=%s msg=%s", fid, code, ec, body.get("msg"))
        try:
            return ErrCode(int(ec))
        except ValueError:
            raise CenturyAPIError(f"unexpected err_code={ec} msg={body.get('msg')!r}")
