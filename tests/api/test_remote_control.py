"""Share-link lifecycle for the remote-control screen.

The invariant worth protecting is that the two Redis keys stay in agreement:
one maps token→instance (what a helper's request resolves through), the other
maps instance→token (what the dashboard shows and reuses). A rename or a
one-sided write on either would either hand out a link that resolves to nothing
or leave a revoked token alive.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from api.services import remote_control

if TYPE_CHECKING:
    import redis


def test_issue_is_stable_so_links_already_sent_keep_working(
    redis_sync: redis.Redis,
) -> None:
    first, _ = remote_control.issue(redis_sync, "bs1")
    second, _ = remote_control.issue(redis_sync, "bs1")
    assert first == second
    assert remote_control.resolve(redis_sync, first) == "bs1"


def test_each_instance_gets_its_own_link(redis_sync: redis.Redis) -> None:
    one, _ = remote_control.issue(redis_sync, "bs1")
    two, _ = remote_control.issue(redis_sync, "bs2")
    assert one != two
    assert remote_control.resolve(redis_sync, two) == "bs2"


def test_revoke_kills_the_token_not_just_the_instance_entry(
    redis_sync: redis.Redis,
) -> None:
    token, _ = remote_control.issue(redis_sync, "bs1")
    remote_control.revoke(redis_sync, "bs1")
    assert remote_control.resolve(redis_sync, token) is None
    assert remote_control.current_token(redis_sync, "bs1") == (None, 0)


def test_unknown_token_resolves_to_nothing(redis_sync: redis.Redis) -> None:
    assert remote_control.resolve(redis_sync, "deadbeef") is None
    assert remote_control.resolve(redis_sync, "") is None


def test_link_expires(redis_sync: redis.Redis) -> None:
    token, ttl = remote_control.issue(redis_sync, "bs1", ttl_s=60)
    assert ttl == 60
    assert 0 < redis_sync.ttl(f"wos:ui:remote:token:{token}") <= 60


@pytest.mark.parametrize("subscribed", [True, False])
def test_tap_reports_whether_a_worker_was_listening(
    redis_sync: redis.Redis, subscribed: bool
) -> None:
    """``delivered == 0`` is how the helper's page shows "bot offline"."""
    channel = remote_control.TAP_CHANNEL_FMT.format(instance_id="bs1")
    pubsub = redis_sync.pubsub()
    if subscribed:
        pubsub.subscribe(channel)
        pubsub.get_message(timeout=1.0)  # drain the subscribe confirmation

    delivered = remote_control.publish_tap(redis_sync, "bs1", 0.25, 0.75)

    assert delivered == (1 if subscribed else 0)
    if subscribed:
        msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
        assert msg is not None
        assert json.loads(msg["data"]) == {"x": 0.25, "y": 0.75}
        pubsub.close()
