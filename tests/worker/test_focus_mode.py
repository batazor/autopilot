"""Roundtrip for the focus_mode Redis helper (sync read/set/clear)."""

from __future__ import annotations

import pytest

from worker import focus_mode


@pytest.mark.integration
def test_focus_set_read_clear_roundtrip(redis_sync) -> None:
    assert focus_mode.read_focus(redis_sync, "bs1") == ("", "")

    focus_mode.set_focus(
        redis_sync, "bs1", scenario="event.fishing_tournament", player="401227964"
    )
    scenario, player = focus_mode.read_focus(redis_sync, "bs1")
    assert scenario == "event.fishing_tournament"
    assert player == "401227964"
    # focus_at stamped for observability.
    assert redis_sync.hget("wos:instance:bs1:state", "focus_at")

    focus_mode.clear_focus(redis_sync, "bs1")
    assert focus_mode.read_focus(redis_sync, "bs1") == ("", "")
