"""Discord-backed gift-code source for beta game shards.

Uses Discord's REST API with a bot token and reads recent channel messages.
The source is intentionally narrow: fetch messages, extract likely gift-code
tokens, and persist them into the shared ``gift_codes`` SQLite table under the
caller-provided game id (for example ``wos_beta``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from config.giftcodes_db import code_exists, upsert_code

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

_DISCORD_API_BASE = "https://discord.com/api/v10"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_TOKEN_ENV = "GIFT_CODES_DISCORD_BOT_TOKEN"
_FALLBACK_TOKEN_ENV = "DISCORD_BOT_TOKEN"
_LIMIT_ENV = "GIFT_CODES_DISCORD_FETCH_LIMIT"
_CODE_RE_ENV = "GIFT_CODES_DISCORD_CODE_RE"

_BACKTICK_CODE_RE = re.compile(
    r"`{1,3}\s*([A-Za-z0-9][A-Za-z0-9_-]{3,47})\s*`{1,3}"
)
_LABELED_CODE_RE = re.compile(
    r"(?i)\b(?:gift\s*code|promo\s*code|redeem\s*code|code|код)\b"
    r"\s*[:：#-]?\s*`?([A-Za-z0-9][A-Za-z0-9_-]{3,47})`?"
)


@dataclass
class NullGiftRedeemSummary:
    """Summary shape compatible with the normal redeemer, but with no work."""

    results: list[Any] = field(default_factory=list)

    def counts_by_status(self) -> dict[str, int]:
        return {}

    def to_dict(self) -> dict[str, Any]:
        return {"total": 0, "counts": {}, "results": []}


def _clean_code(raw: str) -> str:
    return str(raw or "").strip().strip("`'\".,:;()[]{}<>")


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        code = _clean_code(value)
        if not code:
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(code)
    return out


def _message_blobs(message: dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    content = str(message.get("content") or "").strip()
    if content:
        blobs.append(content)

    for embed in message.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in ("title", "description"):
            text = str(embed.get(key) or "").strip()
            if text:
                blobs.append(text)
        footer = embed.get("footer")
        if isinstance(footer, dict):
            text = str(footer.get("text") or "").strip()
            if text:
                blobs.append(text)
        for embed_field in embed.get("fields") or []:
            if not isinstance(embed_field, dict):
                continue
            for key in ("name", "value"):
                text = str(embed_field.get(key) or "").strip()
                if text:
                    blobs.append(text)
    return blobs


def extract_codes_from_text(text: str, *, code_re: re.Pattern[str] | None = None) -> list[str]:
    """Extract likely gift-code tokens from one Discord text blob."""
    if not text:
        return []
    found: list[str] = []
    if code_re is not None:
        found.extend(
            match.group(1) if match.groups() else match.group(0)
            for match in code_re.finditer(text)
        )
    for regex in (_LABELED_CODE_RE, _BACKTICK_CODE_RE):
        found.extend(match.group(1) for match in regex.finditer(text))
    return _ordered_unique(found)


def extract_codes_from_message(
    message: dict[str, Any],
    *,
    code_re: re.Pattern[str] | None = None,
) -> list[str]:
    found: list[str] = []
    for blob in _message_blobs(message):
        found.extend(extract_codes_from_text(blob, code_re=code_re))
    return _ordered_unique(found)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default


def _compile_env_regex() -> re.Pattern[str] | None:
    raw = os.environ.get(_CODE_RE_ENV, "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw)
    except re.error:
        logger.exception("invalid %s regex; ignoring", _CODE_RE_ENV)
        return None


def discord_token(*, token_env: str = _TOKEN_ENV) -> str:
    return (os.environ.get(token_env) or os.environ.get(_FALLBACK_TOKEN_ENV) or "").strip()


class DiscordMessageClient:
    def __init__(
        self,
        *,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._transport = transport

    async def fetch_channel_messages(
        self,
        channel_id: str,
        *,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "autopilot-gift-codes (https://github.com/openai/codex)",
        }
        params = {"limit": str(limit)}
        url = f"{_DISCORD_API_BASE}/channels/{channel_id}/messages"
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                delay = _retry_after_seconds(resp)
                logger.warning("Discord gift-code source rate-limited; retrying after %.1fs", delay)
                await asyncio.sleep(delay)
                resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _retry_after_seconds(resp: httpx.Response) -> float:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    raw = body.get("retry_after") if isinstance(body, dict) else None
    if raw is None:
        raw = resp.headers.get("Retry-After") or resp.headers.get("X-RateLimit-Reset-After")
    try:
        return max(0.1, min(float(raw), 30.0))
    except (TypeError, ValueError):
        return 1.0


async def poll_discord_channel_once(
    *,
    game: str,
    channel_env: str,
    token_env: str = _TOKEN_ENV,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Fetch recent Discord messages and upsert newly discovered codes."""
    token = discord_token(token_env=token_env)
    channel_id = os.environ.get(channel_env, "").strip()
    if not token or not channel_id:
        logger.debug(
            "Discord gift-code source disabled for %s: missing token or %s",
            game,
            channel_env,
        )
        return []

    limit = _env_int(_LIMIT_ENV, _DEFAULT_LIMIT)
    code_re = _compile_env_regex()
    client = DiscordMessageClient(token=token, transport=transport)
    messages = await client.fetch_channel_messages(channel_id, limit=limit)

    found: list[str] = []
    for message in messages:
        found.extend(extract_codes_from_message(message, code_re=code_re))

    added: list[str] = []
    for code in _ordered_unique(found):
        if code_exists(code, game=game):
            continue
        upsert_code(code, game=game)
        added.append(code)
        logger.info("New %s Discord gift code discovered: %s", game, code)
    return added
