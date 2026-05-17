from __future__ import annotations

from ui.views.click_approvals.pending import (
    _is_stale_from_previous_worker,
    _is_stale_navigation_approval,
)


def test_pending_payload_created_before_worker_start_is_stale() -> None:
    payload = {
        "status": "waiting",
        "created_at": "100.0",
        "context": {"scenario": "who_i_am"},
    }
    row = {"worker_started_at": "120.0"}

    assert _is_stale_from_previous_worker(payload, row) is True


def test_pending_payload_created_after_worker_start_is_active() -> None:
    payload = {
        "status": "waiting",
        "created_at": "130.0",
        "context": {"scenario": "ads_rookie_value_pack"},
    }
    row = {"worker_started_at": "120.0"}

    assert _is_stale_from_previous_worker(payload, row) is False


def test_decided_pending_payload_is_not_auto_cleared() -> None:
    payload = {
        "status": "approved",
        "created_at": "100.0",
        "context": {"scenario": "who_i_am"},
    }
    row = {"worker_started_at": "120.0"}

    assert _is_stale_from_previous_worker(payload, row) is False


def test_navigation_pending_is_stale_when_live_screen_already_moved() -> None:
    payload = {
        "status": "waiting",
        "context": {
            "approval_source": "navigation",
            "approval_from_screen": "isNewPeople",
            "approval_to_screen": "main_city",
        },
    }
    row = {"current_screen": "main_city"}

    assert _is_stale_navigation_approval(payload, row) is True


def test_navigation_pending_is_active_while_still_on_from_screen() -> None:
    payload = {
        "status": "waiting",
        "context": {
            "approval_source": "navigation",
            "approval_from_screen": "isNewPeople",
            "approval_to_screen": "main_city",
        },
    }
    row = {"current_screen": "isNewPeople"}

    assert _is_stale_navigation_approval(payload, row) is False
