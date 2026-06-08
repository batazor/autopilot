"""``_resolve_push_delay_seconds`` contract: hh:mm:ss literal or state-field ref."""
from __future__ import annotations

from typing import Any

import pytest

import config.event_timers as event_timers_module
import config.state_store as state_store_module
from config.event_timers import store_event_timer
from config.state_sqlite import set_state_db_path_for_tests
from config.state_store import get_state_store
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
    player_id: str | None = None,
) -> float | None:
    return await _resolve_push_delay_seconds(
        delay,
        instance_id=instance_id,
        redis_async=_FakeRedis(store),
        player_id=player_id,
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
async def test_sqlite_event_timer_artisans_trove_delay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.db"
    set_state_db_path_for_tests(db_path)
    monkeypatch.setattr(state_store_module, "_global_store", None)
    fixed_now = 1_700_000_000.0
    monkeypatch.setattr(event_timers_module.time, "time", lambda: fixed_now)
    try:
        assert store_event_timer(
            player_id="42",
            event_name="shop.artisans_trove",
            raw_text="1d 09:11:19",
            remaining_s=119479,
            recorded_at=fixed_now,
            source_region="artisans_trove.delay",
            confidence=0.93,
        )
        store = {
            "wos:instance:inst-1:state": {"active_player": "42"},
        }
        assert await _resolve("shop.artisans_trove", store=store) == 119479.0

        player = get_state_store().get("42")
        assert player is not None
        timer = player.snapshot().event_timers["shop.artisans_trove"]
        assert timer.remaining_s == 119479
        assert timer.recorded_at == fixed_now
        assert timer.reset_at == fixed_now + 119479
        assert timer.raw_text == "1d 09:11:19"
        assert timer.confidence == 0.93
    finally:
        set_state_db_path_for_tests(None)
        state_store_module._global_store = None


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


@pytest.mark.asyncio
async def test_delay_expression_round_trip_literal() -> None:
    # Mercenary Prestige idiom: march there and back (×2) + a 3s window.
    # 45s one-way → 45*2 + 3 = 93s.
    assert await _resolve("00:45 * 2 + 3s") == 93.0
    assert await _resolve("00:45*2+3s") == 93.0  # whitespace-insensitive


@pytest.mark.asyncio
async def test_delay_expression_with_state_field() -> None:
    # mp_ttl read by a prior ``ocr ... store: mp_ttl`` (raw mm:ss) → 60s.
    # 60*2 + 3 = 123s.
    store = {
        "wos:instance:inst-1:state": {"active_player": "p42"},
        "wos:player:p42:state": {"mp_ttl": "01:00"},
    }
    assert await _resolve("mp_ttl * 2 + 3s", store=store) == 123.0


@pytest.mark.asyncio
async def test_delay_expression_operator_precedence() -> None:
    # ``*`` binds tighter than ``+``: 30 + 10*2 = 50, not 80.
    assert await _resolve("30s + 10s * 2") == 50.0
    # Division + subtraction.
    assert await _resolve("60s / 2 - 5s") == 25.0


@pytest.mark.asyncio
async def test_delay_expression_unresolved_operand_skips() -> None:
    # A missing field operand collapses the whole expression to None (skip push),
    # so a missed OCR never re-fires on a degenerate delay.
    assert await _resolve("missing_field * 2 + 3s") is None


@pytest.mark.asyncio
async def test_delay_expression_malformed_skips() -> None:
    # Garbage / unbalanced expressions are skipped, not crashed.
    assert await _resolve("00:45 * * 2") is None
    assert await _resolve("00:45 2 +") is None


@pytest.mark.asyncio
async def test_delay_expression_clamps_negative_to_zero() -> None:
    # A negative result clamps to 0.0 (enqueue immediately) rather than going back in time.
    assert await _resolve("10s - 30s") == 0.0
