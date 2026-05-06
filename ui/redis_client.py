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


class InstanceStateRow(TypedDict, total=False):
    state: str
    active_player: str
    paused: str
    worker_started_at: str
    current_task_type: str
    current_task_id: str
    current_task_player: str
    current_task_started_at: str


class FsmHistoryEntry(TypedDict):
    ts: float
    state: str


_COOPERATIVE_TASKS = frozenset({"defend_ally", "beast"})


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
    return int(client.zcard("wos:queue"))


def count_claimed_slots(client: redis.Redis) -> int:
    n = 0
    for _ in client.scan_iter("wos:claimed:*"):
        n += 1
    return n


def fetch_queue_rows(client: redis.Redis) -> list[QueueRow]:
    raw_items = client.zrangebyscore("wos:queue", "-inf", "+inf", withscores=True)
    rows: list[QueueRow] = []
    for payload, score in raw_items:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        task_type = str(data.get("task_type", ""))
        reg = data.get("region")
        region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
        rows.append(
            QueueRow(
                task_id=str(data.get("task_id", "")),
                player_id=str(data.get("player_id", "")),
                task_type=task_type,
                priority=int(data.get("priority", 0)),
                scheduled_at=float(data.get("run_at", score)),
                instance_id=str(data.get("instance_id", "")),
                cooperative=task_type in _COOPERATIVE_TASKS,
                region=region,
            )
        )
    return rows


def remove_queue_task(client: redis.Redis, task_id: str) -> bool:
    payloads = client.zrangebyscore("wos:queue", "-inf", "+inf")
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if str(data.get("task_id", "")) == task_id:
            client.zrem("wos:queue", payload)
            return True
    return False


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


def fetch_fsm_history(client: redis.Redis, player_id: str, limit: int = 20) -> list[FsmHistoryEntry]:
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


def fetch_logs(client: redis.Redis, instance_id: str, limit: int = 200) -> list[str]:
    key = f"wos:log:{instance_id}"
    items = client.lrange(key, 0, limit - 1)
    return [str(x) for x in items]


def clear_logs(client: redis.Redis, instance_id: str) -> None:
    client.delete(f"wos:log:{instance_id}")
