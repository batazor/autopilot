"""Tests for Discord-backed beta gift-code scraping."""

from __future__ import annotations

import httpx
import pytest

from century.gift_codes import discord_source
from century.gift_codes.discord_source import (
    DISCORD_TOKEN_SETTING_KEY,
    extract_codes_from_message,
    poll_discord_channel_once,
)
from config.giftcodes_db import code_exists, set_gift_code_setting


def test_extract_codes_from_message_content_and_embeds() -> None:
    message = {
        "content": "Gift code: BETA123 and also `BACKTICK42`",
        "embeds": [
            {
                "title": "WOS Beta",
                "description": "Код: RU7777",
                "fields": [{"name": "Redeem code", "value": "code: EMBED999"}],
            }
        ],
    }

    assert extract_codes_from_message(message) == [
        "BETA123",
        "BACKTICK42",
        "RU7777",
        "EMBED999",
    ]


def test_extract_codes_from_multiline_discord_announcement() -> None:
    message = {
        "content": """🎁 New CDK Gift Code
gongce198cny100Kstars


Gift codes：
1000keys
368gearbox
dragoncastle
cloudbeast
dragonframe
fireworksplate
6480stars""",
    }

    assert extract_codes_from_message(message) == [
        "gongce198cny100Kstars",
        "1000keys",
        "368gearbox",
        "dragoncastle",
        "cloudbeast",
        "dragonframe",
        "fireworksplate",
        "6480stars",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("game", "channel_env", "expected_channel_id"),
    [
        ("wos_beta", "WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID", "1511081143083077652"),
        (
            "kingshot_beta",
            "KINGSHOT_BETA_GIFT_CODES_DISCORD_CHANNEL_ID",
            "1513031288695558285",
        ),
    ],
)
async def test_poll_discord_channel_once_upserts_new_codes(
    monkeypatch: pytest.MonkeyPatch,
    game: str,
    channel_env: str,
    expected_channel_id: str,
) -> None:
    monkeypatch.setenv("GIFT_CODES_DISCORD_BOT_TOKEN", "ignored-env-token")
    monkeypatch.setenv(channel_env, "999999999")
    set_gift_code_setting(DISCORD_TOKEN_SETTING_KEY, "token-1")

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v10/channels/{expected_channel_id}/messages"
        assert request.url.params["limit"] == "50"
        assert request.headers["Authorization"] == "Bot token-1"
        return httpx.Response(
            200,
            json=[
                {"id": "2", "content": "code: BETA123"},
                {"id": "1", "content": "`BETA456`"},
            ],
        )

    added = await poll_discord_channel_once(
        game=game,
        channel_env=channel_env,
        transport=httpx.MockTransport(_handler),
    )
    again = await poll_discord_channel_once(
        game=game,
        channel_env=channel_env,
        transport=httpx.MockTransport(_handler),
    )

    assert added == ["BETA123", "BETA456"]
    assert again == []
    assert code_exists("BETA123", game=game)
    assert code_exists("BETA456", game=game)


@pytest.mark.asyncio
async def test_poll_discord_channel_once_falls_back_to_user_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user token (rejected as ``Bot <token>``) is retried bare and cached."""
    channel_env = "KINGSHOT_BETA_GIFT_CODES_DISCORD_CHANNEL_ID"
    user_token = "user-token-xyz"
    discord_source._AUTH_SCHEME_CACHE.pop(user_token, None)
    set_gift_code_setting(DISCORD_TOKEN_SETTING_KEY, user_token)

    seen_auth: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        seen_auth.append(auth)
        if auth.startswith("Bot "):
            return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
        return httpx.Response(200, json=[{"id": "1", "content": "code: USERTOK1"}])

    added = await poll_discord_channel_once(
        game="kingshot_beta",
        channel_env=channel_env,
        transport=httpx.MockTransport(_handler),
    )

    # First poll probes the bot scheme, then the raw user-token scheme.
    assert seen_auth == [f"Bot {user_token}", user_token]
    assert added == ["USERTOK1"]
    assert code_exists("USERTOK1", game="kingshot_beta")
    assert discord_source._AUTH_SCHEME_CACHE[user_token] == "raw"

    # A subsequent poll skips the failing bot probe thanks to the cache.
    seen_auth.clear()
    await poll_discord_channel_once(
        game="kingshot_beta",
        channel_env=channel_env,
        transport=httpx.MockTransport(_handler),
    )
    assert seen_auth == [user_token]


@pytest.mark.asyncio
async def test_poll_discord_channel_once_disabled_without_ui_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIFT_CODES_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID", raising=False)

    added = await poll_discord_channel_once(
        game="wos_beta",
        channel_env="WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID",
    )

    assert added == []
