"""RedisQueue.schedule publishes dashboard queue events on successful enqueue."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler.queue import RedisQueue


@pytest.fixture
def mock_settings() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_schedule_publishes_on_success(mock_settings: MagicMock) -> None:
    redis = AsyncMock()
    redis.register_script.return_value = AsyncMock(return_value=1)
    queue = RedisQueue(redis, mock_settings)

    with patch(
        "dashboard.dashboard_events.publish_dashboard_event_async",
        new_callable=AsyncMock,
    ) as publish:
        ok = await queue.schedule(
            task_id="t1",
            player_id="p1",
            task_type="heroes/claim",
            priority=100,
            run_at=1_700_000_000.0,
            instance_id="inst-1",
        )

    assert ok is True
    publish.assert_awaited_once_with(
        redis,
        topic="queue",
        instance_id="inst-1",
        reason="enqueue",
    )


@pytest.mark.asyncio
async def test_schedule_skips_publish_on_dedup(mock_settings: MagicMock) -> None:
    redis = AsyncMock()
    queue = RedisQueue(redis, mock_settings)
    queue._dedup_zadd_script = AsyncMock(return_value=0)

    with patch(
        "dashboard.dashboard_events.publish_dashboard_event_async",
        new_callable=AsyncMock,
    ) as publish:
        ok = await queue.schedule(
            task_id="t1",
            player_id="p1",
            task_type="heroes/claim",
            priority=100,
            run_at=1_700_000_000.0,
            instance_id="inst-1",
            skip_if_duplicate=True,
        )

    assert ok is False
    publish.assert_not_awaited()
    queue._dedup_zadd_script.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_publishes_after_dedup_script_applies(mock_settings: MagicMock) -> None:
    redis = AsyncMock()
    queue = RedisQueue(redis, mock_settings)
    queue._dedup_zadd_script = AsyncMock(return_value=1)

    with patch(
        "dashboard.dashboard_events.publish_dashboard_event_async",
        new_callable=AsyncMock,
    ) as publish:
        ok = await queue.schedule(
            task_id="t1",
            player_id="",
            task_type="cron_task",
            priority=50,
            run_at=1_700_000_000.0,
            instance_id="inst-2",
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )

    assert ok is True
    publish.assert_awaited_once_with(
        redis,
        topic="queue",
        instance_id="inst-2",
        reason="enqueue",
    )
