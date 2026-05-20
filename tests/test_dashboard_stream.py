"""Dashboard SSE revision fingerprints."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from api.services.dashboard_stream import (
    approval_revision,
    notifications_revision,
    queue_revision,
)


def test_queue_revision_changes_when_pending_count_changes():
    client = MagicMock()
    view_a = {"pending_count": 1, "running": [], "history": []}
    view_b = {"pending_count": 2, "running": [], "history": []}
    with patch(
        "api.services.dashboard_stream.queue_api.build_queue_view",
        side_effect=[view_a, view_b],
    ):
        assert queue_revision(client) != queue_revision(client)


def test_approval_revision_stable_without_pending():
    client = MagicMock()
    with (
        patch(
            "api.services.dashboard_stream.get_pending",
            return_value=None,
        ),
        patch(
            "api.services.dashboard_stream.get_instance_state",
            return_value={"current_screen": "main_city"},
        ),
        patch(
            "api.services.dashboard_stream.click_approval_enabled",
            return_value=True,
        ),
    ):
        a = approval_revision(client, "inst-1")
        b = approval_revision(client, "inst-1")
    assert a == b


def test_approval_revision_changes_when_pending_appears():
    client = MagicMock()
    pending = {"trace_id": "abc", "type": "tap"}
    with (
        patch(
            "api.services.dashboard_stream.get_pending",
            side_effect=[None, pending],
        ),
        patch(
            "api.services.dashboard_stream.get_instance_state",
            return_value={},
        ),
        patch(
            "api.services.dashboard_stream.click_approval_enabled",
            return_value=True,
        ),
        patch(
            "api.services.dashboard_stream._trace_id_from_payload",
            return_value="abc",
        ),
    ):
        idle = approval_revision(client, "inst-1")
        active = approval_revision(client, "inst-1")
    assert idle != active


def test_notifications_revision_uses_tail_id():
    client = MagicMock()
    items_a = [{"id": "n1"}]
    items_b = [{"id": "n1"}, {"id": "n2"}]
    with patch(
        "api.services.dashboard_stream.notifications_api.list_notifications",
        side_effect=[items_a, items_b],
    ):
        assert notifications_revision(client, "inst-1") != notifications_revision(
            client, "inst-1"
        )
