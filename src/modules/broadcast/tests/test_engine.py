"""Selection-engine unit tests (pure — no Redis, no DB, no device)."""
from __future__ import annotations

from modules.broadcast.engine import (
    cron_interval_seconds,
    message_due,
    min_gap_seconds,
    select_due_message,
)
from modules.broadcast.models import (
    SCOPE_ALL,
    SCOPE_KINGSHOT,
    SCOPE_WOS,
    TRIGGER_CRON,
    TRIGGER_EVENT,
    BroadcastMessage,
)


def _cron(id_: str, cron: str, *, priority: int = 100, cooldown: int = 0, scope: str = SCOPE_ALL) -> BroadcastMessage:
    return BroadcastMessage(
        id=id_, title=id_, text="hi", trigger_kind=TRIGGER_CRON, cron=cron,
        cooldown_minutes=cooldown, priority=priority, game_scope=scope,
    )


def _event(id_: str, cond: str, *, priority: int = 100, cooldown: int = 360, scope: str = SCOPE_ALL) -> BroadcastMessage:
    return BroadcastMessage(
        id=id_, title=id_, text="hi", trigger_kind=TRIGGER_EVENT, cond=cond,
        cooldown_minutes=cooldown, priority=priority, game_scope=scope,
    )


def test_cron_interval_parsing() -> None:
    assert cron_interval_seconds("*/15 * * * *") == 15 * 60
    assert cron_interval_seconds("15 */8 * * *") == 8 * 3600
    assert cron_interval_seconds("0 */12 * * *") == 12 * 3600
    # Unsupported shapes → 0 (never fire).
    assert cron_interval_seconds("0 0 * * *") == 0
    assert cron_interval_seconds("") == 0
    assert cron_interval_seconds("*/0 * * * *") == 0


def test_cron_message_due_respects_interval() -> None:
    msg = _cron("m", "*/15 * * * *")
    now = 10_000.0
    assert message_due(msg, {}, now, None) is True          # never sent → due
    assert message_due(msg, {}, now, now - 60) is False     # 1 min ago → too soon
    assert message_due(msg, {}, now, now - 16 * 60) is True  # past the interval


def test_invalid_cron_never_fires() -> None:
    assert message_due(_cron("m", "0 0 * * *"), {}, 1.0, None) is False


def test_event_message_fires_only_when_flag_set() -> None:
    msg = _event("e", "event_bear_hunt == 1")
    assert message_due(msg, {"event_bear_hunt": 1}, 1.0, None) is True
    assert message_due(msg, {"event_bear_hunt": 0}, 1.0, None) is False
    # Missing flag → eval_cond returns False (calendar hasn't read it yet).
    assert message_due(msg, {}, 1.0, None) is False


def test_event_cooldown_respected() -> None:
    msg = _event("e", "event_bear_hunt == 1", cooldown=360)  # 6h
    state = {"event_bear_hunt": 1}
    now = 100_000.0
    assert message_due(msg, state, now, now - 60) is False          # still cooling
    assert message_due(msg, state, now, now - 7 * 3600) is True     # window elapsed


def test_disabled_message_never_due() -> None:
    msg = _cron("m", "*/15 * * * *")
    disabled = BroadcastMessage(**{**msg.to_dict(), "enabled": False})
    assert message_due(disabled, {}, 1.0, None) is False


def test_min_gap_uses_max_of_cooldown_and_cron() -> None:
    # cooldown 30m vs cron 8h → 8h wins.
    assert min_gap_seconds(_cron("m", "15 */8 * * *", cooldown=30)) == 8 * 3600
    # event uses its cooldown.
    assert min_gap_seconds(_event("e", "x == 1", cooldown=120)) == 120 * 60


def test_select_priority_tiebreak_and_scope() -> None:
    msgs = [
        _cron("low", "*/15 * * * *", priority=50),
        _cron("high", "*/15 * * * *", priority=10),
        _cron("ks_only", "*/15 * * * *", priority=1, scope=SCOPE_KINGSHOT),
    ]
    chosen = select_due_message(msgs, {}, 10_000.0, {}, SCOPE_WOS)
    # ks_only is highest priority but wrong scope → excluded; "high" wins.
    assert chosen is not None
    assert chosen.id == "high"


def test_select_returns_none_when_nothing_due() -> None:
    msgs = [_event("e", "event_x == 1")]
    assert select_due_message(msgs, {}, 1.0, {}, SCOPE_WOS) is None
