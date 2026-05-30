"""`worker_alive` derivation for the approval preview placeholder.

Distinguishes "bot running, capture warming up" from "bot stopped" using the
worker's ``last_seen_at`` heartbeat freshness.
"""
from __future__ import annotations

import time

from api.services import click_approval_store as store


def test_fresh_heartbeat_is_alive() -> None:
    assert store._worker_recently_seen({"last_seen_at": str(time.time())}) is True


def test_stale_heartbeat_is_not_alive() -> None:
    old = time.time() - store._WORKER_ALIVE_WINDOW_S - 5.0
    assert store._worker_recently_seen({"last_seen_at": str(old)}) is False


def test_missing_heartbeat_is_not_alive() -> None:
    assert store._worker_recently_seen({}) is False


def test_unparseable_heartbeat_is_not_alive() -> None:
    assert store._worker_recently_seen({"last_seen_at": ""}) is False
    assert store._worker_recently_seen({"last_seen_at": "not-a-number"}) is False


def test_boundary_just_inside_window_is_alive() -> None:
    recent = time.time() - (store._WORKER_ALIVE_WINDOW_S - 1.0)
    assert store._worker_recently_seen({"last_seen_at": str(recent)}) is True
