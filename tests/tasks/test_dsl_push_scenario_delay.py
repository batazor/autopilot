"""``_resolve_push_delay_seconds`` contract: hh:mm:ss literal or state-field ref."""
from __future__ import annotations

from typing import Any

import pytest

from tasks.dsl_scenario_helpers import _resolve_push_delay_seconds


class _FakeRedis:
    """Minimal async stub: ``hget`` over an in-memory ``{key: {field: value}}`` map."""

    def __init__(self, store: dict[str, dict[str, Any]] | None = None) -> None:
        self._store = store or {}

    async def hget(self, key: str, field: str) -> Any:
        return self._store.get(key, {}).get(field)


async def _resolve(
    delay: object,
    *,
    store: dict[str, dict[str, Any]] | None = None,
    instance_id: str = "inst-1",
) -> float | None:
    return await _resolve_push_delay_seconds(
        delay,
        instance_id=instance_id,
        redis_async=_FakeRedis(store),
    )


@pytest.mark.asyncio
async def test_missing_delay_is_zero() -> None:
    assert await _resolve(None) == 0.0
    assert await _resolve("") == 0.0
    assert await _resolve("   ") == 0.0


@pytest.mark.asyncio
async def test_hms_literal_delay() -> None:
    assert await _resolve("00:05:30") == 330.0
    assert await _resolve("01:23:45") == 5025.0
    assert await _resolve("05:30") == 330.0  # mm:ss
    # Artisan's Trove cooldown sample (2h 18m 11s).
    assert await _resolve("02:18:11") == 8291.0


@pytest.mark.asyncio
async def test_state_field_artisans_trove_delay() -> None:
    # End-to-end: scenario does ``ocr: artisans_trove.delay`` (default ``store:``
    # uses the region name), then ``push_scenario: delay: artisans_trove.delay``
    # reads "02:18:11" from player state → 8291 seconds.
    store = {
        "wos:instance:inst-1:state": {"active_player": "p42"},
        "wos:player:p42:state": {"artisans_trove.delay": "02:18:11"},
    }
    assert await _resolve("artisans_trove.delay", store=store) == 8291.0


@pytest.mark.asyncio
async def test_hms_literal_invalid_returns_none() -> None:
    # ``delay`` was specified but unparseable → return None so the caller
    # skips the enqueue (DSL-level guard against tight zero-delay loops).
    assert await _resolve("00:99") is None
    assert await _resolve("not:a:time") is None


@pytest.mark.asyncio
async def test_state_field_ref_player_scope() -> None:
    store = {
        "wos:instance:inst-1:state": {"active_player": "p42"},
        "wos:player:p42:state": {"artisan_trove.ttl": "01:02:03"},
    }
    assert await _resolve("artisan_trove.ttl", store=store) == 3723.0


@pytest.mark.asyncio
async def test_state_field_ref_instance_fallback() -> None:
    # No active player → fall back to instance state.
    store = {
        "wos:instance:inst-1:state": {"some_timer": "00:01:00"},
    }
    assert await _resolve("some_timer", store=store) == 60.0


@pytest.mark.asyncio
async def test_state_field_ref_unset_returns_none() -> None:
    # Field missing → None: caller skips the push (no tight zero-delay loop).
    store = {"wos:instance:inst-1:state": {"active_player": "p42"}}
    assert await _resolve("missing_field", store=store) is None


@pytest.mark.asyncio
async def test_state_field_ref_not_hms_returns_none() -> None:
    # Stored value must be hh:mm:ss; int seconds / garbage → None + warning.
    store = {
        "wos:instance:inst-1:state": {"active_player": "p42"},
        "wos:player:p42:state": {"timer_int": "4995", "timer_garbage": "abc"},
    }
    assert await _resolve("timer_int", store=store) is None
    assert await _resolve("timer_garbage", store=store) is None


@pytest.mark.asyncio
async def test_numeric_delay_rejected() -> None:
    # Bare numbers are NOT a valid delay — only hh:mm:ss / suffix literal or
    # state ref. A bare int/float is stringified; "60" has no ":" and no
    # suffix → treated as state field name, which won't resolve in an empty
    # store → None (skip enqueue).
    assert await _resolve(60) is None
    assert await _resolve("60") is None


@pytest.mark.asyncio
async def test_suffix_literal_delay() -> None:
    # ``Ns`` / ``Nms`` / ``Nm`` / ``Nh`` map straight through ``_parse_wait_seconds``.
    assert await _resolve("500ms") == 0.5
    assert await _resolve("30s") == 30.0
    assert await _resolve("15m") == 900.0
    assert await _resolve("6h") == 21600.0
    assert await _resolve("1.5h") == 5400.0
