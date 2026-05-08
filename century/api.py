"""Century Game API client for Whiteout Survival.

Endpoints:
  POST /api/player      — player info (login step)
  POST /api/captcha     — get CAPTCHA image
  POST /api/gift_code   — redeem gift code (returns err_code + msg)

All requests are signed with MD5:
  sign = md5(sorted_params_string + SALT)
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from enum import IntEnum

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_API_BASE = "https://wos-giftcode-api.centurygame.com/api"
_SALT = "tB87#kPtkxqOS2"
_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://wos-giftcode.centurygame.com",
    "referer": "https://wos-giftcode.centurygame.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
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


def _timestamp() -> str:
    return str(int(time.time()))


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


def _retry_century_error(exc: BaseException) -> bool:
    return not isinstance(exc, CenturyAPIError)


_CENTURY_RETRY = retry(
    retry=retry_if_exception(_retry_century_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=4),
)


def _raise_for_status(resp: httpx.Response, *, endpoint: str) -> None:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        text = exc.response.text.strip()
        if len(text) > 200:
            text = f"{text[:197]}..."
        raise CenturyAPIError(
            f"{endpoint} HTTP {status}: {text or exc.response.reason_phrase}"
        ) from exc


class CenturyClient:
    """Async HTTP client for the Century Game gift code API."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Player info
    # ------------------------------------------------------------------

    @_CENTURY_RETRY
    async def fetch_player(self, fid: int) -> PlayerData:
        ts = _timestamp()
        sign = _sign(("fid", str(fid)), ("time", ts))
        data = {"fid": str(fid), "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=_HEADERS, timeout=self._timeout) as client:
            resp = await client.post(f"{_API_BASE}/player", data=data)
            _raise_for_status(resp, endpoint="player")
            body = resp.json()

        if body.get("msg", "").lower() != "success":
            raise CenturyAPIError(
                f"player fetch failed: {body.get('msg')} err_code={body.get('err_code')}"
            )

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

    @_CENTURY_RETRY
    async def fetch_captcha(self, fid: int) -> CaptchaData:
        ts = _timestamp()
        sign = _sign(("fid", str(fid)), ("init", "0"), ("time", ts))
        data = {"fid": str(fid), "init": "0", "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=_HEADERS, timeout=self._timeout) as client:
            resp = await client.post(f"{_API_BASE}/captcha", data=data)
            _raise_for_status(resp, endpoint="captcha")
            body = resp.json()

        if body.get("msg", "").upper() != "SUCCESS":
            raise CenturyAPIError(f"captcha request failed: {body.get('msg')}")

        return CaptchaData(img_b64=body["data"]["img"])

    # ------------------------------------------------------------------
    # Gift code redemption
    # ------------------------------------------------------------------

    async def redeem(self, fid: int, code: str, captcha_code: str) -> tuple[ErrCode, str]:
        ts = _timestamp()
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
            _raise_for_status(resp, endpoint="gift_code")
            body = resp.json()

        ec_raw = body.get("err_code", -1)
        msg = str(body.get("msg") or "")
        logger.debug("redeem fid=%d code=%s ec=%s msg=%s", fid, code, ec_raw, msg)
        try:
            ec = ErrCode(int(ec_raw))
        except ValueError as exc:
            raise CenturyAPIError(
                f"unexpected err_code={ec_raw} msg={body.get('msg')!r}"
            ) from exc
        return ec, msg
