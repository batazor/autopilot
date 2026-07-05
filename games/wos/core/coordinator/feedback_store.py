"""Redis persistence for the feedback loop — the missing producer/consumer wiring.

:mod:`feedback` is pure (fold outcomes → derive bias); nothing durable ever fed
it in production, so the coordinator planned every tick with amnesia. This module
closes the loop across processes:

* the **worker** calls :func:`record_outcome` when a planner-dispatched task
  finishes (``_record_task_history`` — it already knows success/reason/duration),
* the **scheduler** calls :func:`load_feedback` at the top of the march tick so
  :func:`feedback.tuning` sees the accumulated history.

Storage: one hash per player — ``wos:player:{fid}:action_feedback`` — field =
action key (``intel:run``), value = compact JSON of :class:`feedback.ActionStat`.
Per-player because action keys are per-account state (bs3's broken nav must not
back off bs1). No read-modify-write races in practice: one worker per instance
serially executes that player's tasks, and the scheduler only reads.

Best-effort by design: every function swallows Redis errors (a flap must never
fail the task path or the planner tick — worst case the bot plans without
memory for one tick, which is exactly the old behaviour).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .feedback import ActionStat, FeedbackState, Outcome, record

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

FEEDBACK_RETENTION_SECONDS = 7 * 24 * 3600


def feedback_key(player_id: str) -> str:
    return f"wos:player:{player_id}:action_feedback"


def _stat_to_json(st: ActionStat) -> str:
    return json.dumps(
        {
            "domain": st.domain,
            "attempts": st.attempts,
            "progressed": st.progressed,
            "consecutive_stalls": st.consecutive_stalls,
            "last_ts": st.last_ts,
            "last_reason": st.last_reason,
            "same_reason_streak": st.same_reason_streak,
        },
        separators=(",", ":"),
    )


def _stat_from_json(key: str, raw: str) -> ActionStat | None:
    try:
        data: dict[str, Any] = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ActionStat(
            key=key,
            domain=str(data.get("domain") or ""),
            attempts=int(data.get("attempts") or 0),
            progressed=int(data.get("progressed") or 0),
            consecutive_stalls=int(data.get("consecutive_stalls") or 0),
            last_ts=float(data.get("last_ts") or 0.0),
            last_reason=str(data.get("last_reason") or ""),
            same_reason_streak=int(data.get("same_reason_streak") or 0),
        )
    except (TypeError, ValueError):
        return None


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


async def load_feedback(redis: Redis, player_id: str) -> FeedbackState:
    """The player's accumulated per-action stats (empty state on any failure)."""
    try:
        raw = await redis.hgetall(feedback_key(player_id))
    except Exception:
        logger.debug("feedback load failed player=%s", player_id, exc_info=True)
        return FeedbackState()
    stats: dict[str, ActionStat] = {}
    for k, v in (raw or {}).items():
        key = _decode(k)
        st = _stat_from_json(key, _decode(v))
        if st is not None:
            stats[key] = st
    return FeedbackState(stats=stats)


async def record_outcome(redis: Redis, player_id: str, outcome: Outcome) -> None:
    """Fold one task outcome into the player's durable feedback hash."""
    key = feedback_key(player_id)
    try:
        prev_raw = await redis.hget(key, outcome.key)
        prev = _stat_from_json(outcome.key, _decode(prev_raw)) if prev_raw else None
        state = FeedbackState(stats={outcome.key: prev} if prev else {})
        updated = record(state, outcome).stats[outcome.key]
        pipe = redis.pipeline(transaction=False)
        pipe.hset(key, outcome.key, _stat_to_json(updated))
        pipe.expire(key, FEEDBACK_RETENTION_SECONDS)
        await pipe.execute()
    except Exception:
        logger.debug(
            "feedback record failed player=%s key=%s", player_id, outcome.key, exc_info=True
        )
