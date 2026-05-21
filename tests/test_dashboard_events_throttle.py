"""Throttled dashboard pub/sub."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ui.dashboard_events import publish_dashboard_event_throttled_async


@pytest.mark.asyncio
async def test_throttled_publish_skips_second_call_within_window():
    client = AsyncMock()
    client.set.side_effect = [True, False]

    with patch(
        "ui.dashboard_events.publish_dashboard_event_async",
        new_callable=AsyncMock,
    ) as publish:
        await publish_dashboard_event_throttled_async(
            client,
            topic="instance",
            instance_id="inst-1",
            min_interval_s=1,
        )
        await publish_dashboard_event_throttled_async(
            client,
            topic="instance",
            instance_id="inst-1",
            min_interval_s=1,
        )

    publish.assert_awaited_once()
