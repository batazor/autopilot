"""Dashboard SSE revision fingerprints."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from api.services.dashboard_stream import (
    approval_revision,
    fleet_revision,
    instance_revision,
    notifications_revision,
    player_revision,
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


def test_player_revision_changes_when_field_updates():
    client = MagicMock()
    state_a = {"nickname": "A", "stove_level": "1"}
    state_b = {"nickname": "B", "stove_level": "1"}
    with patch(
        "api.services.dashboard_stream.get_player_state_hash",
        side_effect=[state_a, state_b],
    ):
        assert player_revision(client, "player-1") != player_revision(client, "player-1")


def test_fleet_revision_changes_when_screen_changes():
    client = MagicMock()
    state_a = {"current_screen": "main_city", "last_seen_at": "1000"}
    state_b = {"current_screen": "arena", "last_seen_at": "1000"}
    inst = MagicMock(instance_id="inst-1")
    with (
        patch("api.services.dashboard_stream.load_settings") as load_s,
        patch("api.services.dashboard_stream.load_devices") as load_d,
        patch(
            "api.services.dashboard_stream.get_instance_state",
            side_effect=[state_a, state_b],
        ),
        patch("api.services.dashboard_stream.count_queue_tasks", return_value=0),
        patch("api.services.dashboard_stream.count_claimed_slots", return_value=0),
        patch(
            "api.services.dashboard_stream.fleet.count_live_instances",
            return_value=(1, 0, 0),
        ),
        patch("api.services.dashboard_stream.fleet.fleet_status", return_value="live"),
    ):
        load_s.return_value.instances = [inst]
        load_d.return_value.devices = []
        a = fleet_revision(client)
        b = fleet_revision(client)
    assert a != b


def test_instance_revision_changes_on_preview_mtime():
    client = MagicMock()
    path = MagicMock()
    path.is_file.return_value = True
    path.stat.return_value.st_mtime = 1.0
    row = {"current_screen": "main_city"}
    with (
        patch(
            "api.services.dashboard_stream.get_instance_state",
            return_value=row,
        ),
        patch(
            "api.services.dashboard_stream.count_queue_tasks_for_instance",
            return_value=0,
        ),
        patch(
            "api.services.dashboard_stream.rolling_live_preview_path",
            return_value=path,
        ),
        patch(
            "api.services.dashboard_stream.fetch_queue_history_rows",
            return_value=[],
        ),
        patch("api.services.dashboard_stream.fleet.fleet_status", return_value="live"),
    ):
        a = instance_revision(client, "inst-1")
        path.stat.return_value.st_mtime = 2.0
        b = instance_revision(client, "inst-1")
    assert a != b


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
