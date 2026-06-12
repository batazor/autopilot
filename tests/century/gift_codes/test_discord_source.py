"""Tests for Discord-backed beta gift-code scraping."""

from __future__ import annotations

import httpx
import pytest

from century.gift_codes.discord_source import (
    extract_codes_from_message,
    poll_discord_channel_once,
)
from config.giftcodes_db import code_exists


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


@pytest.mark.asyncio
async def test_poll_discord_channel_once_upserts_new_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIFT_CODES_DISCORD_BOT_TOKEN", "token-1")
    monkeypatch.setenv("WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID", "123456789")

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v10/channels/123456789/messages"
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
        game="wos_beta",
        channel_env="WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID",
        transport=httpx.MockTransport(_handler),
    )
    again = await poll_discord_channel_once(
        game="wos_beta",
        channel_env="WOS_BETA_GIFT_CODES_DISCORD_CHANNEL_ID",
        transport=httpx.MockTransport(_handler),
    )

    assert added == ["BETA123", "BETA456"]
    assert again == []
    assert code_exists("BETA123", game="wos_beta")
    assert code_exists("BETA456", game="wos_beta")


@pytest.mark.asyncio
async def test_poll_discord_channel_once_disabled_without_env(
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
