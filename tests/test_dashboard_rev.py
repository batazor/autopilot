"""Dashboard revision Redis cache."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from api.services.dashboard_rev import (
    REV_QUEUE_KEY,
    get_cached_revision,
    invalidate_revision_for_topic,
    store_revision,
)


def test_store_and_get_cached_revision():
    client = MagicMock()
    client.get.return_value = b"abc123"
    assert get_cached_revision(client, REV_QUEUE_KEY) == "abc123"
    store_revision(client, REV_QUEUE_KEY, "deadbeef")
    client.set.assert_called_once_with(REV_QUEUE_KEY, "deadbeef", ex=300)


def test_invalidate_queue_also_clears_fleet():
    client = MagicMock()
    invalidate_revision_for_topic(client, topic="queue")
    assert client.delete.call_count == 2


def test_invalidate_queue_with_instance_id_also_clears_instance():
    client = MagicMock()
    invalidate_revision_for_topic(client, topic="queue", instance_id="inst-1")
    assert client.delete.call_count == 3
    client.delete.assert_any_call("wos:dashboard:rev:instance:inst-1")


def test_invalidate_instance_requires_instance_id():
    client = MagicMock()
    invalidate_revision_for_topic(client, topic="instance")
    client.delete.assert_not_called()
    invalidate_revision_for_topic(client, topic="instance", instance_id="inst-1")
    client.delete.assert_called_once_with("wos:dashboard:rev:instance:inst-1")


def test_invalidate_player_requires_player_id():
    client = MagicMock()
    invalidate_revision_for_topic(client, topic="player", player_id="pid-9")
    client.delete.assert_called_once_with("wos:dashboard:rev:player:pid-9")


def test_queue_revision_uses_cache_when_enabled():
    client = MagicMock()
    with (
        patch(
            "api.services.dashboard_stream.get_cached_revision",
            return_value="cached-rev",
        ),
        patch("api.services.dashboard_stream.queue_api.build_queue_view") as build,
    ):
        from api.services.dashboard_stream import queue_revision

        rev = queue_revision(client, use_cache=True)
    assert rev == "cached-rev"
    build.assert_not_called()
