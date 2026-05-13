"""Sync Redis helpers for Streamlit (reads settings from config)."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypedDict

import redis
import streamlit as st

from config.loader import load_settings
from config.redis_health import format_redis_unreachable_message


@dataclass(frozen=True)
class QueueRow:
    task_id: str
    player_id: str
    task_type: str
    priority: int
    scheduled_at: float
    instance_id: str
    cooperative: bool
    region: str | None = None
    payload: dict[str, object] | None = None


@dataclass(frozen=True)
class RunningQueueRow:
    task_id: str
    player_id: str
    task_type: str
    priority: int
    instance_id: str
    started_at: float
    region: str | None = None
    payload: dict[str, object] | None = None


@dataclass(frozen=True)
class QueueHistoryRow:
    task_id: str
    task_type: str
    scenario: str
    player_id: str
    instance_id: str
    priority: int
    started_at: float
    finished_at: float
    duration_s: float
    success: bool
    region: str | None = None
    reason: str = ""
    error: str = ""
    payload: dict[str, object] | None = None
    # DSL scenario execution trace (from ``metadata``); None for non-DSL tasks.
    scenario_completed: bool | None = None
    steps_total: int | None = None
    steps_trace: list[dict[str, Any]] | None = None


class InstanceStateRow(TypedDict, total=False):
    state: str
    active_player: str
    paused: str
    worker_started_at: str
    last_seen_at: str
    last_error: str
    current_task_player: str
    current_task_started_at: str


class FsmHistoryEntry(TypedDict):
    ts: float
    state: str


_COOPERATIVE_TASKS = frozenset({"defend_ally", "beast"})


def _queue_key(instance_id: str) -> str:
    iid = str(instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


def _history_key(instance_id: str) -> str:
    return f"wos:queue:history:{str(instance_id or '').strip()}"


def _parse_queue_row(payload: str, score: float) -> QueueRow | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    task_type = str(data.get("task_type", ""))
    reg = data.get("region")
    region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
    return QueueRow(
        task_id=str(data.get("task_id", "")),
        player_id=str(data.get("player_id", "")),
        task_type=task_type,
        priority=int(data.get("priority", 0)),
        scheduled_at=float(data.get("run_at", score)),
        instance_id=str(data.get("instance_id", "")),
        cooperative=task_type in _COOPERATIVE_TASKS,
        region=region,
        payload=data,
    )


def _parse_history_row(payload: str) -> QueueHistoryRow | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reg = data.get("region")
    region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
    try:
        started_at = float(data.get("started_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        started_at = 0.0
    try:
        finished_at = float(data.get("finished_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        finished_at = 0.0
    try:
        duration_s = float(data.get("duration_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration_s = max(0.0, finished_at - started_at)
    meta_raw = data.get("metadata")
    meta_d: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
    sc_done = meta_d.get("scenario_completed")
    scenario_completed: bool | None
    scenario_completed = sc_done if isinstance(sc_done, bool) else None
    steps_total: int | None
    try:
        st_raw = meta_d.get("steps_total")
        steps_total = int(st_raw) if st_raw is not None else None
    except (TypeError, ValueError):
        steps_total = None
    tr_raw = meta_d.get("steps_trace")
    steps_trace: list[dict[str, Any]] | None = None
    if isinstance(tr_raw, list):
        steps_trace = [x for x in tr_raw if isinstance(x, dict)]
    return QueueHistoryRow(
        task_id=str(data.get("task_id", "")),
        task_type=str(data.get("task_type", "")),
        scenario=str(data.get("scenario", "") or data.get("task_type", "")),
        player_id=str(data.get("player_id", "")),
        instance_id=str(data.get("instance_id", "")),
        priority=int(data.get("priority", 0) or 0),
        started_at=started_at,
        finished_at=finished_at,
        duration_s=duration_s,
        success=bool(data.get("success", False)),
        region=region,
        reason=str(data.get("reason", "") or ""),
        error=str(data.get("error", "") or ""),
        payload=data,
        scenario_completed=scenario_completed,
        steps_total=steps_total,
        steps_trace=steps_trace,
    )


@st.cache_resource
def get_redis() -> redis.Redis:
    settings = load_settings()
    return redis.Redis.from_url(settings.redis.url, decode_responses=True)


def require_redis_connection() -> redis.Redis:
    """Ping Redis once; on failure show Streamlit error and stop the script run."""
    settings = load_settings()
    client = get_redis()
    try:
        client.ping()
    except redis.RedisError as exc:
        st.error(format_redis_unreachable_message(settings.redis.url, exc))
        st.stop()
    return client


def fetch_queue_explain_rows(
    *,
    instance_id: str,
    current_screen: str = "",
    n: int = 10,
    redis_url: str | None = None,
) -> list[dict[str, Any]]:
    """Top-N ranked due candidates with the full ``effective_priority`` breakdown.

    Sync wrapper around :meth:`scheduler.queue.RedisQueue.explain_top_n` for
    Streamlit — opens a short-lived async client per call (the page already
    refreshes on a fragment timer, so a fresh connection costs nothing on top
    of that cadence). Mirrors ``scripts/queue_explain.py``: read-only, no
    mutation, safe against a live system.

    Returns ``[]`` on any failure so the UI can show "no candidates" instead
    of crashing the fragment.
    """
    import asyncio

    import redis.asyncio as aioredis

    from scheduler.queue import RedisQueue

    url = redis_url or load_settings().redis.url

    async def _run() -> list[dict[str, Any]]:
        aclient = aioredis.from_url(url, decode_responses=True)
        try:
            q = RedisQueue(aclient)
            return await q.explain_top_n(
                instance_id, current_screen=current_screen, n=n
            )
        finally:
            await aclient.aclose()

    try:
        return asyncio.run(_run())
    except Exception:
        return []


def count_queue_tasks(client: redis.Redis) -> int:
    def _is_queue_zset_key(k: str) -> bool:
        if not k:
            return False
        if ":running" in k or ":idx:" in k:
            return False
        try:
            return str(client.type(k) or "").lower() == "zset"
        except redis.RedisError:
            return False

    total = 0
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if not _is_queue_zset_key(k):
            continue
        try:
            total += int(client.zcard(k))
        except redis.RedisError:
            continue
    return total


def count_claimed_slots(client: redis.Redis) -> int:
    n = 0
    for _ in client.scan_iter("wos:claimed:*"):
        n += 1
    return n


def fetch_queue_rows(client: redis.Redis) -> list[QueueRow]:
    def _is_queue_zset_key(k: str) -> bool:
        if not k:
            return False
        if ":running" in k or ":idx:" in k:
            return False
        try:
            return str(client.type(k) or "").lower() == "zset"
        except redis.RedisError:
            return False

    keys: list[str] = []
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if _is_queue_zset_key(k):
            keys.append(k)

    rows: list[QueueRow] = []
    for key in keys:
        try:
            raw_items = client.zrangebyscore(key, "-inf", "+inf", withscores=True)
        except redis.RedisError:
            continue
        for payload, score in raw_items:
            row = _parse_queue_row(payload, float(score))
            if row is not None:
                rows.append(row)
    return rows


def _running_row_from_instance_state(
    client: redis.Redis, instance_id: str
) -> RunningQueueRow | None:
    """Synthesize a RunningQueueRow from ``wos:instance:<iid>:state``.

    The worker publishes ``wos:queue:running:<iid>`` with a 180s TTL and
    never refreshes it, so long-running tasks (e.g. ``building.upgrade``)
    vanish from that key while the instance state still shows ``busy``.
    Treat the state hash as the source of truth in that case so the UI
    keeps reporting what's active.
    """
    try:
        raw = client.hgetall(f"wos:instance:{instance_id}:state") or {}
    except redis.RedisError:
        return None
    state_map: dict[str, str] = {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else (str(v) if v is not None else "")
        )
        for k, v in raw.items()
    }
    if state_map.get("state", "").strip().lower() != "busy":
        return None
    scenario = state_map.get("current_scenario", "").strip()
    task_id = state_map.get("current_task_id", "").strip()
    task_type = state_map.get("current_task_type", "").strip() or scenario
    player = state_map.get("current_task_player", "").strip()
    if not scenario and not player and not task_id:
        return None
    try:
        started_at = float(state_map.get("current_task_started_at") or 0.0)
    except (TypeError, ValueError):
        started_at = 0.0
    region = state_map.get("current_task_region", "").strip() or None
    return RunningQueueRow(
        task_id=task_id or "(running)",
        player_id=player,
        task_type=task_type or "(unknown)",
        priority=0,
        instance_id=instance_id,
        started_at=started_at,
        region=region,
        payload=None,
    )


def fetch_running_queue_row(
    client: redis.Redis, *, instance_id: str | None = None
) -> RunningQueueRow | None:
    key = f"wos:queue:running:{instance_id}" if instance_id else "wos:queue:running"
    raw = client.get(key)
    if not raw:
        # Running key TTL (180s) often outlasts long tasks — fall back to
        # the per-instance state hash, which the worker keeps current.
        return _running_row_from_instance_state(client, instance_id) if instance_id else None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reg = data.get("region")
    region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
    try:
        started_at = float(data.get("started_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        started_at = 0.0
    return RunningQueueRow(
        task_id=str(data.get("task_id", "")),
        player_id=str(data.get("player_id", "")),
        task_type=str(data.get("task_type", "")),
        priority=int(data.get("priority", 0)),
        instance_id=str(data.get("instance_id", "")),
        started_at=started_at,
        region=region,
        payload=data,
    )


def fetch_queue_history_rows(
    client: redis.Redis, *, instance_id: str, limit: int = 20
) -> list[QueueHistoryRow]:
    try:
        raw_items = client.lrange(_history_key(instance_id), 0, max(0, int(limit) - 1))
    except redis.RedisError:
        return []
    rows: list[QueueHistoryRow] = []
    for payload in raw_items:
        row = _parse_history_row(str(payload))
        if row is not None:
            rows.append(row)
    return rows


def remove_queue_task(client: redis.Redis, task_id: str) -> bool:
    def _is_queue_zset_key(k: str) -> bool:
        if not k:
            return False
        if ":running" in k or ":idx:" in k:
            return False
        try:
            return str(client.type(k) or "").lower() == "zset"
        except redis.RedisError:
            return False

    keys: list[str] = []
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if _is_queue_zset_key(k):
            keys.append(k)
    for key in keys:
        payloads = client.zrangebyscore(key, "-inf", "+inf")
        for payload in payloads:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if str(data.get("task_id", "")) == task_id:
                client.zrem(key, payload)
                return True
    return False


def run_queue_task_now(client: redis.Redis, task_id: str) -> bool:
    """Re-score a queued task to ``time.time()`` so the next scheduler tick picks
    it up immediately. Also rewrites the in-payload ``run_at`` field so the row
    is internally consistent. Returns ``True`` if the task was found.
    """
    def _is_queue_zset_key(k: str) -> bool:
        if not k:
            return False
        if ":running" in k or ":idx:" in k:
            return False
        try:
            return str(client.type(k) or "").lower() == "zset"
        except redis.RedisError:
            return False

    keys: list[str] = []
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if _is_queue_zset_key(k):
            keys.append(k)
    now = time.time()
    for key in keys:
        payloads = client.zrangebyscore(key, "-inf", "+inf")
        for payload in payloads:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if str(data.get("task_id", "")) != task_id:
                continue
            data["run_at"] = now
            new_payload = json.dumps(data, ensure_ascii=False)
            # ZSET members are byte-exact: rescore = ZREM old + ZADD new.
            client.zrem(key, payload)
            client.zadd(key, {new_payload: now})
            return True
    return False


def count_queue_tasks_for_instance(client: redis.Redis, *, instance_id: str) -> int:
    """Count queued items for a single instance."""
    return int(client.zcard(_queue_key(instance_id)))


def clear_queue_tasks(client: redis.Redis) -> int:
    """Drop all pending queue items across instances.

    Returns the count of pending tasks that were removed. Preserves
    ``wos:queue:history:*`` (so the execution history table stays intact)
    and ``wos:queue:running:*`` (so an in-flight task can still report
    completion without orphaning state).

    Also wipes any legacy ``wos:queue:idx:*`` SETs from the deprecated dedup
    index — those are no longer written but may linger in long-lived Redis
    instances. They get blanket-deleted along with the ZSET queues.
    """
    removed_total = 0
    targets: list[str] = []
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if ":history" in k or ":running" in k:
            continue
        try:
            ktype = str(client.type(k) or "").lower()
        except redis.RedisError:
            continue
        if ktype == "zset":
            with suppress(redis.RedisError):
                removed_total += int(client.zcard(k))
        targets.append(k)
    if targets:
        with suppress(redis.RedisError):
            client.delete(*targets)
    return removed_total


def fetch_next_queue_row_for_instance(
    client: redis.Redis, *, instance_id: str
) -> QueueRow | None:
    """Fetch the earliest scheduled row (by ZSET score) for an instance."""
    items = client.zrange(_queue_key(instance_id), 0, 0, withscores=True)
    if not items:
        return None
    payload, score = items[0]
    return _parse_queue_row(payload, float(score))


def get_instance_state(client: redis.Redis, instance_id: str) -> dict[str, str]:
    """All hash fields at ``wos:instance:{id}:state`` (worker publishes paused, task, uptime)."""
    key = f"wos:instance:{instance_id}:state"
    raw = client.hgetall(key)
    if not raw:
        return {}
    return {str(k): str(v) if v is not None else "" for k, v in raw.items()}


def get_player_state_hash(client: redis.Redis, player_id: str) -> dict[str, str]:
    """All hash fields at ``wos:player:<id>:state`` (nickname, ``buildings.levels.*``, OCR, …)."""
    pid = str(player_id or "").strip()
    if not pid:
        return {}
    try:
        raw = client.hgetall(f"wos:player:{pid}:state") or {}
    except redis.RedisError:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else (str(v) if v is not None else "")
        )
        for k, v in raw.items()
    }


def get_player_fsm(client: redis.Redis, player_id: str) -> str:
    key = f"wos:player:{player_id}:state"
    raw = client.hget(key, "fsm_state")
    return str(raw) if raw else ""


def get_player_scenario(client: redis.Redis, player_id: str) -> str | None:
    key = f"wos:player:{player_id}:scenario"
    raw = client.get(key)
    if raw is None or raw == "":
        return None
    return str(raw)


def set_player_scenario(client: redis.Redis, player_id: str, scenario_id: str | None) -> None:
    key = f"wos:player:{player_id}:scenario"
    if scenario_id is None or scenario_id == "":
        client.delete(key)
    else:
        client.set(key, scenario_id)


def dsl_preempt_gen_key(instance_id: str) -> str:
    """Redis key: monotonic counter bumped when debug UI enqueues \"Run scenario now\"."""

    return f"wos:instance:{instance_id}:dsl_preempt_gen"


def bump_dsl_preempt_generation(client: redis.Redis, instance_id: str) -> int:
    """Increment so any running DSL task can cooperatively exit before the next queue pop."""

    return int(client.incr(dsl_preempt_gen_key(instance_id)))


def push_instance_command(client: redis.Redis, instance_id: str, cmd: dict[str, object]) -> None:
    client.lpush(f"wos:ui:command:{instance_id}", json.dumps(cmd))


def push_scheduler_command(client: redis.Redis, cmd: dict[str, object]) -> None:
    client.lpush("wos:ui:command:scheduler", json.dumps(cmd))


def fetch_fsm_history(
    client: redis.Redis, player_id: str, limit: int = 20
) -> list[FsmHistoryEntry]:
    key = f"wos:player:{player_id}:fsm_history"
    items = client.lrange(key, 0, limit - 1)
    out: list[FsmHistoryEntry] = []
    for raw in items:
        try:
            data = json.loads(raw)
            ts = float(data.get("ts", 0))
            state = str(data.get("state", ""))
            out.append(FsmHistoryEntry(ts=ts, state=state))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out
