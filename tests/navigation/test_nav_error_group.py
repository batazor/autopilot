"""The ``nav_error`` group moves as one, or it lies.

`nav_error` gained machine-readable siblings — `nav_error_cause`,
`nav_error_at`, `nav_error_route`. They were written by the DSL gate with a raw
`hset` while `NavStateStore.clear_error` only knew about `nav_error`, so after a
failure followed by a success the prose line was blank and `nav_error_cause`
still held the stale cause. A reader trusting the machine-readable field — the
entire reason it exists — got a lie.

Both sides now go through the store, and these tests pin that they agree on how
many fields there are.
"""

from __future__ import annotations

from typing import Any

import pytest

from navigation.nav_state import NavStateStore

_GROUP = ("nav_error", "nav_error_cause", "nav_error_route", "nav_error_at")


class _FakeRedis:
    def __init__(self) -> None:
        self.hash: dict[str, str] = {}

    async def hset(
        self,
        _key: str,
        field: str | None = None,
        value: str | None = None,
        *,
        mapping: dict[str, str] | None = None,
    ) -> None:
        if mapping is not None:
            self.hash.update(mapping)
        elif field is not None:
            self.hash[field] = value or ""


@pytest.fixture
def store() -> tuple[NavStateStore, _FakeRedis]:
    fake = _FakeRedis()
    return NavStateStore(fake), fake


@pytest.mark.asyncio
async def test_write_populates_the_whole_group(
    store: tuple[NavStateStore, _FakeRedis],
) -> None:
    nav_state, fake = store

    await nav_state.write_error(
        "bs1",
        "navigation_failed: main_city → arena (no_route)",
        cause="no_route",
        route_explain="route main_city -> arena\nselected: unreachable",
    )

    for field in _GROUP:
        assert fake.hash.get(field), f"{field} was not written"
    assert fake.hash["nav_error_cause"] == "no_route"
    assert "unreachable" in fake.hash["nav_error_route"]
    assert float(fake.hash["nav_error_at"]) > 0


@pytest.mark.asyncio
async def test_clear_empties_the_whole_group(
    store: tuple[NavStateStore, _FakeRedis],
) -> None:
    """THE regression: clearing only `nav_error` left a stale cause behind."""
    nav_state, fake = store
    await nav_state.write_error("bs1", "boom", cause="tap_blocked", route_explain="r")

    await nav_state.clear_error("bs1")

    leftovers = {f: fake.hash.get(f, "") for f in _GROUP if fake.hash.get(f)}
    assert not leftovers, f"stale after clear: {leftovers}"


@pytest.mark.asyncio
async def test_a_second_failure_replaces_rather_than_merges(
    store: tuple[NavStateStore, _FakeRedis],
) -> None:
    """A route-less failure after a tap-blocked one must not inherit its route."""
    nav_state, fake = store
    await nav_state.write_error(
        "bs1", "first", cause="no_route", route_explain="route a -> b"
    )

    await nav_state.write_error("bs1", "second", cause="tap_blocked")

    assert fake.hash["nav_error_cause"] == "tap_blocked"
    assert fake.hash["nav_error_route"] == ""


@pytest.mark.asyncio
async def test_write_without_a_cause_still_stamps_a_time() -> None:
    """Legacy call shape (detail only) must not produce a half-filled group."""
    fake = _FakeRedis()

    await NavStateStore(fake).write_error("bs1", "plain detail")

    assert fake.hash["nav_error"] == "plain detail"
    assert fake.hash["nav_error_cause"] == ""
    assert float(fake.hash["nav_error_at"]) > 0


@pytest.mark.asyncio
async def test_no_redis_is_a_no_op() -> None:
    """Navigation must never crash because a state write failed."""
    nav_state = NavStateStore(None)

    await nav_state.write_error("bs1", "boom", cause="no_route")
    await nav_state.clear_error("bs1")


@pytest.mark.asyncio
async def test_transport_errors_are_swallowed() -> None:
    class _Boom:
        async def hset(self, *_a: Any, **_k: Any) -> None:
            msg = "redis down"
            raise RuntimeError(msg)

    nav_state = NavStateStore(_Boom())

    await nav_state.write_error("bs1", "boom", cause="no_route")
    await nav_state.clear_error("bs1")
