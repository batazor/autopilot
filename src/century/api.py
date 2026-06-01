"""Century Game gift-code API client (WOS + Kingshot).

The protocol is identical across games:

  POST /api/player      — player info (login step)
  POST /api/captcha     — get CAPTCHA image (WOS only)
  POST /api/gift_code   — redeem gift code (returns err_code + msg)

What varies per game is captured in ``GameConfig`` (host, MD5 salt, whether
captcha exists, time unit for ``/api/gift_code``). Pass one to ``CenturyClient``
at construction.

All payloads are signed: ``sign = md5(sorted_params_string + game.salt)``.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from enum import IntEnum

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from century.games import WOS, GameConfig
from century.headers import build_headers

logger = logging.getLogger(__name__)


class ErrCode(IntEnum):
    SUCCESS = 20000
    TIMEOUT_RETRY = 40004           # Kingshot — server-side timeout, retry the request
    ALREADY_RECEIVED_1 = 40008
    ALREADY_RECEIVED_2 = 40011
    ALREADY_RECEIVED_3 = 40005      # "USED" — player already claimed this code
    CDK_EXPIRED = 40007
    CDK_NOT_FOUND = 40014
    STOVE_LEVEL_TOO_LOW = 40006     # furnace/town-center level requirement not met
    RECHARGE_MONEY = 40017          # Kingshot — VIP-only code, account VIP too low
    RECHARGE_MONEY_VIP = 40018      # Kingshot — VIP-only code, account VIP too low
    CAPTCHA_TOO_FREQUENT = 40101    # WOS only
    CAPTCHA_ERROR = 40103           # WOS only


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


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
    def __init__(
        self,
        message: str,
        *,
        err_code: int | str | None = None,
        api_msg: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.err_code = str(err_code) if err_code is not None else None
        self.api_msg = api_msg
        self.endpoint = endpoint


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
        msg = f"{endpoint} HTTP {status}: {text or exc.response.reason_phrase}"
        raise CenturyAPIError(
            msg
        ) from exc


class CenturyClient:
    """Async HTTP client for the Century Game gift-code API."""

    def __init__(self, game: GameConfig = WOS, timeout: float = 30.0) -> None:
        self._game = game
        self._timeout = timeout
        # Headers are fixed per client instance: same browser identity for
        # every call avoids the "UA flipping mid-session" tell.
        self._headers = build_headers(origin=game.redemption_url)

    @property
    def game(self) -> GameConfig:
        return self._game

    # ------------------------------------------------------------------
    # Signing helpers
    # ------------------------------------------------------------------

    def _sign(self, *pairs: tuple[str, str]) -> str:
        param_str = "&".join(f"{k}={v}" for k, v in sorted(pairs))
        return _md5(param_str + self._game.salt)

    @staticmethod
    def _ts_seconds() -> str:
        return str(int(time.time()))

    def _ts(self) -> str:
        """API timestamp in the game's expected unit (KS ms, WOS seconds).

        Applies to ``/api/player`` and ``/api/gift_code`` alike — the real
        Kingshot browser client sends millisecond timestamps to both, so we
        mirror that. ``redeem_time_unit`` carries the per-game unit.
        """
        now = time.time()
        if self._game.redeem_time_unit == "ms":
            return str(int(now * 1000))
        return str(int(now))

    # ------------------------------------------------------------------
    # Player info
    # ------------------------------------------------------------------

    @_CENTURY_RETRY
    async def fetch_player(self, fid: int) -> PlayerData:
        ts = self._ts()
        sign = self._sign(("fid", str(fid)), ("time", ts))
        data = {"fid": str(fid), "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.post(f"{self._game.base_url}/player", data=data)
            _raise_for_status(resp, endpoint="player")
            body = resp.json()

        if body.get("msg", "").lower() != "success":
            api_msg = str(body.get("msg") or "")
            err_code = body.get("err_code")
            msg = f"player fetch failed: {api_msg} err_code={err_code}"
            raise CenturyAPIError(
                msg,
                err_code=err_code,
                api_msg=api_msg,
                endpoint="player",
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
    # Captcha (WOS only — Kingshot's API does not require captcha)
    # ------------------------------------------------------------------

    @_CENTURY_RETRY
    async def fetch_captcha(self, fid: int) -> CaptchaData:
        if not self._game.has_captcha:
            msg = f"{self._game.id}: gift-code API has no captcha endpoint"
            raise CenturyAPIError(msg)

        ts = self._ts_seconds()
        sign = self._sign(("fid", str(fid)), ("init", "0"), ("time", ts))
        data = {"fid": str(fid), "init": "0", "time": ts, "sign": sign}

        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.post(f"{self._game.base_url}/captcha", data=data)
            _raise_for_status(resp, endpoint="captcha")
            body = resp.json()

        if body.get("msg", "").upper() != "SUCCESS":
            msg = f"captcha request failed: {body.get('msg')}"
            raise CenturyAPIError(msg)

        return CaptchaData(img_b64=body["data"]["img"])

    # ------------------------------------------------------------------
    # Gift code redemption
    # ------------------------------------------------------------------

    async def redeem(
        self, fid: int, code: str, captcha_code: str | None = None
    ) -> tuple[ErrCode, str]:
        """POST ``/api/gift_code``.

        ``captcha_code`` is required when ``game.has_captcha`` is True (WOS);
        ignored otherwise (Kingshot — the API accepts the raw cdk + fid).
        """
        ts = self._ts()
        if self._game.has_captcha:
            if not captcha_code:
                msg = f"{self._game.id}: captcha_code required"
                raise CenturyAPIError(msg)
            sign = self._sign(
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
        else:
            # Kingshot: no captcha, but the browser client still sends an empty
            # ``captcha_code`` and includes it in the sign — omitting it yields a
            # different MD5 and the server rejects the request.
            sign = self._sign(
                ("captcha_code", ""), ("cdk", code), ("fid", str(fid)), ("time", ts)
            )
            data = {
                "fid": str(fid),
                "cdk": code,
                "captcha_code": "",
                "time": ts,
                "sign": sign,
            }

        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as client:
            resp = await client.post(f"{self._game.base_url}/gift_code", data=data)
            _raise_for_status(resp, endpoint="gift_code")
            body = resp.json()

        ec_raw = body.get("err_code", -1)
        msg = str(body.get("msg") or "")
        logger.debug("redeem game=%s fid=%d code=%s ec=%s msg=%s",
                     self._game.id, fid, code, ec_raw, msg)
        try:
            ec = ErrCode(int(ec_raw))
        except ValueError as exc:
            msg_0 = f"unexpected err_code={ec_raw} msg={body.get('msg')!r}"
            raise CenturyAPIError(
                msg_0
            ) from exc
        return ec, msg
