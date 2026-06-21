"""Dashboard event publish invalidates revision cache (async Redis)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dashboard.dashboard_events import publish_dashboard_event_async


@pytest.mark.asyncio
async def test_async_publish_invalidates_instance_revision():
    client = AsyncMock()
    await publish_dashboard_event_async(
        client,
        topic="instance",
        instance_id="inst-1",
        reason="step_index",
    )
    client.publish.assert_awaited_once()
    client.delete.assert_awaited_once_with("wos:dashboard:rev:instance:inst-1")


@pytest.mark.asyncio
async def test_async_publish_invalidates_player_revision():
    client = AsyncMock()
    await publish_dashboard_event_async(
        client,
        topic="player",
        player_id="pid-9",
        reason="ocr_store",
    )
    client.delete.assert_awaited_once_with("wos:dashboard:rev:player:pid-9")


@pytest.mark.asyncio
async def test_async_queue_publish_clears_queue_and_fleet_revisions():
    client = AsyncMock()
    await publish_dashboard_event_async(
        client,
        topic="queue",
        instance_id="inst-1",
        reason="running",
    )
    assert client.delete.await_count == 3
    client.delete.assert_any_await("wos:dashboard:rev:queue")
    client.delete.assert_any_await("wos:dashboard:rev:fleet")
    client.delete.assert_any_await("wos:dashboard:rev:instance:inst-1")
