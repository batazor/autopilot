"""Sync Redis helpers for Streamlit (reads settings from config)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypedDict

import redis
import streamlit as st

from config.loader import load_settings


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
    )


@st.cache_resource
def get_redis() -> redis.Redis:
    settings = load_settings()
    return redis.Redis.from_url(settings.redis.url, decode_responses=True)


def require_redis_connection() -> redis.Redis:
    """Ping Redis once; on failure show Streamlit error and stop the script run."""
    client = get_redis()
    try:
        client.ping()
    except redis.RedisError as exc:
        st.error(
            f"Cannot reach Redis ({exc}). Start the server or fix **redis.url** in config."
        )
        st.stop()
    return client


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


def fetch_running_queue_row(
    client: redis.Redis, *, instance_id: str | None = None
) -> RunningQueueRow | None:
    key = f"wos:queue:running:{instance_id}" if instance_id else "wos:queue:running"
    raw = client.get(key)
    if not raw:
        return None
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
                # Best-effort cleanup of duplicate index (if enabled).
                try:
                    iid = str(data.get("instance_id", "") or "").strip() or "unknown"
                    pid = str(data.get("player_id", "") or "").strip()
                    ttype = str(data.get("task_type", "") or "").strip()
                    reg = str(data.get("region", "") or "").strip()
                    idx_key = f"wos:queue:idx:{iid}:{ttype}:{reg}:{pid}"
                    client.srem(idx_key, payload)
                except Exception:
                    pass
                return True
    return False


def count_queue_tasks_for_instance(client: redis.Redis, *, instance_id: str) -> int:
    """Count queued items for a single instance."""
    return int(client.zcard(_queue_key(instance_id)))


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
