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


def test_queue_route_rebuilds_view_once_on_revision_miss():
    from api.routers.queue import get_queue

    client = MagicMock()
    view = {"pending": [], "running": [], "history": [], "pending_count": 0}
    with (
        patch("api.routers.queue.get_cached_revision", return_value=None),
        patch("api.routers.queue.queue_api.build_queue_view", return_value=view) as build,
        patch("api.routers.queue.store_revision") as store,
    ):
        out = get_queue(client=client, if_revision=None)

    build.assert_called_once_with(client)
    store.assert_called_once()
    assert out["revision"]


def test_queue_route_returns_unchanged_without_rebuild_on_matching_revision():
    from api.routers.queue import get_queue

    client = MagicMock()
    with (
        patch("api.routers.queue.get_cached_revision", return_value="rev-1"),
        patch("api.routers.queue.queue_api.build_queue_view") as build,
    ):
        out = get_queue(client=client, if_revision="rev-1")

    assert out == {"unchanged": True, "revision": "rev-1"}
    build.assert_not_called()


def test_queue_route_uses_in_memory_view_when_revision_cached():
    from api.routers.queue import get_queue

    client = MagicMock()
    view = {"pending": [{"task_id": "t1"}], "running": [], "history": [], "pending_count": 1}
    with (
        patch("api.routers.queue.get_cached_revision", return_value="rev-1"),
        patch("api.routers.queue.queue_api.get_cached_queue_view", return_value=view) as cached,
        patch("api.routers.queue.queue_api.build_queue_view") as build,
    ):
        out = get_queue(client=client, if_revision=None)

    cached.assert_called_once_with("rev-1")
    build.assert_not_called()
    assert out["revision"] == "rev-1"
    assert out["pending_count"] == 1


def test_queue_route_paginates_cached_view():
    from api.routers.queue import get_queue

    client = MagicMock()
    view = {
        "pending": [{"task_id": f"p{i}", "overdue": i % 2 == 0} for i in range(5)],
        "running": [],
        "history": [{"task_id": f"h{i}"} for i in range(4)],
        "pending_count": 5,
        "pending_overdue_count": 3,
        "history_count": 4,
    }
    with (
        patch("api.routers.queue.get_cached_revision", return_value="rev-1"),
        patch("api.routers.queue.queue_api.get_cached_queue_view", return_value=view),
        patch("api.routers.queue.queue_api.build_queue_view") as build,
    ):
        out = get_queue(
            client=client,
            if_revision=None,
            pending_page=2,
            pending_page_size=2,
            history_page=2,
            history_page_size=3,
        )

    build.assert_not_called()
    assert [row["task_id"] for row in out["pending"]] == ["p2", "p3"]
    assert [row["task_id"] for row in out["history"]] == ["h3"]
    assert out["pending_count"] == 5
    assert out["pending_overdue_count"] == 3
    assert out["history_count"] == 4


def test_queue_route_full_cached_view_skips_pagination():
    from api.routers.queue import get_queue

    client = MagicMock()
    view = {
        "pending": [{"task_id": f"p{i}"} for i in range(3)],
        "running": [],
        "history": [{"task_id": f"h{i}"} for i in range(2)],
        "pending_count": 3,
    }
    with (
        patch("api.routers.queue.get_cached_revision", return_value="rev-1"),
        patch("api.routers.queue.queue_api.get_cached_queue_view", return_value=view),
    ):
        out = get_queue(
            client=client,
            if_revision=None,
            pending_page=2,
            pending_page_size=1,
            history_page=2,
            history_page_size=1,
            full=True,
        )

    assert [row["task_id"] for row in out["pending"]] == ["p0", "p1", "p2"]
    assert [row["task_id"] for row in out["history"]] == ["h0", "h1"]
