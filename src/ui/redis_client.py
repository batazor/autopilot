"""Sync Redis helpers for Streamlit (reads settings from config)."""
from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import redis
import streamlit as st

from config.loader import load_settings
from config.redis_health import format_redis_unreachable_message

# ``redis-py`` stubs union sync + async return types as ``ResponseT``
# (``Awaitable[T] | T``). This module uses only the sync client with
# ``decode_responses=True``, so the values are always concrete strs /
# ints / dicts / lists. These tiny ``cast`` wrappers narrow back to the
# concrete sync arm — same approach as in ``adb/approvals.py``.


def _r_get(client: redis.Redis, key: str) -> str | None:
    return cast("str | None", client.get(key))


def _r_hgetall(client: redis.Redis, key: str) -> dict[str, str]:
    return cast("dict[str, str]", client.hgetall(key))


def _r_zcard(client: redis.Redis, key: str) -> int:
    return cast("int", client.zcard(key))


def _r_zrange_with_scores(
    client: redis.Redis, key: str, start: int, stop: int
) -> list[tuple[str, float]]:
    return cast(
        "list[tuple[str, float]]",
        client.zrange(key, start, stop, withscores=True),
    )


def _r_zrange(client: redis.Redis, key: str, start: int, stop: int) -> list[str]:
    return cast("list[str]", client.zrange(key, start, stop))


def _r_lrange(client: redis.Redis, key: str, start: int, stop: int) -> list[str]:
    return cast("list[str]", client.lrange(key, start, stop))


def _r_zrangebyscore(
    client: redis.Redis, key: str, lo: str | float, hi: str | float
) -> list[str]:
    return cast("list[str]", client.zrangebyscore(key, lo, hi))


def _r_zrangebyscore_with_scores(
    client: redis.Redis, key: str, lo: str | float, hi: str | float
) -> list[tuple[str, float]]:
    return cast(
        "list[tuple[str, float]]",
        client.zrangebyscore(key, lo, hi, withscores=True),
    )


def _r_incr(client: redis.Redis, key: str) -> int:
    return cast("int", client.incr(key))


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
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunningQueueRow:
    task_id: str
    player_id: str
    task_type: str
    priority: int
    instance_id: str
    started_at: float
    region: str | None = None
    payload: dict[str, Any] | None = None


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
    trace_id: str = ""
    span_id: str = ""
    payload: dict[str, Any] | None = None
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
        trace_id=str(data.get("trace_id", "") or ""),
        span_id=str(data.get("span_id", "") or ""),
        payload=data,
        scenario_completed=scenario_completed,
        steps_total=steps_total,
        steps_trace=steps_trace,
    )


@st.cache_resource
def get_redis() -> redis.Redis:
    from config.redis_metrics import instrument_redis_client

    settings = load_settings()
    client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    return instrument_redis_client(client, component="ui")


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
    of that cadence). Read-only, no mutation, safe against a live system.

    Returns ``[]`` on any failure so the UI can show "no candidates" instead
    of crashing the fragment.
    """
    import asyncio

    import redis.asyncio as aioredis

    from scheduler.queue import RedisQueue

    url = redis_url or load_settings().redis.url

    async def _run() -> list[dict[str, Any]]:
        from config.redis_metrics import instrument_redis_client

        aclient = aioredis.from_url(url, decode_responses=True)
        instrument_redis_client(aclient, component="ui")
        try:
            q = RedisQueue(aclient, load_settings())
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
            total += _r_zcard(client, k)
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
            raw_items = _r_zrangebyscore_with_scores(client, key, "-inf", "+inf")
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
        raw = _r_hgetall(client, f"wos:instance:{instance_id}:state") or {}
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
    raw = _r_get(client, key)
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
        raw_items = _r_lrange(client, _history_key(instance_id), 0, max(0, int(limit) - 1))
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
        payloads = _r_zrangebyscore(client, key, "-inf", "+inf")
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
        payloads = _r_zrangebyscore(client, key, "-inf", "+inf")
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
    return _r_zcard(client, _queue_key(instance_id))


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
                removed_total += _r_zcard(client, k)
        targets.append(k)
    if targets:
        with suppress(redis.RedisError):
            client.delete(*targets)
    return removed_total


def fetch_next_queue_row_for_instance(
    client: redis.Redis, *, instance_id: str
) -> QueueRow | None:
    """Fetch the earliest scheduled row (by ZSET score) for an instance."""
    items = _r_zrange_with_scores(client, _queue_key(instance_id), 0, 0)
    if not items:
        return None
    payload, score = items[0]
    return _parse_queue_row(payload, float(score))


def get_instance_state(client: redis.Redis, instance_id: str) -> dict[str, str]:
    """All hash fields at ``wos:instance:{id}:state`` (worker publishes paused, task, uptime)."""
    key = f"wos:instance:{instance_id}:state"
    raw = _r_hgetall(client, key)
    if not raw:
        return {}
    return {str(k): str(v) if v is not None else "" for k, v in raw.items()}


def get_player_state_hash(client: redis.Redis, player_id: str) -> dict[str, str]:
    """All hash fields at ``wos:player:<id>:state`` (nickname, ``buildings.levels.*``, OCR, …)."""
    pid = str(player_id or "").strip()
    if not pid:
        return {}
    try:
        raw = _r_hgetall(client, f"wos:player:{pid}:state") or {}
    except redis.RedisError:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else (str(v) if v is not None else "")
        )
        for k, v in raw.items()
    }


def get_player_scenario(client: redis.Redis, player_id: str) -> str | None:
    key = f"wos:player:{player_id}:scenario"
    raw = _r_get(client, key)
    if raw is None or raw == "":
        return None
    return str(raw)


def set_player_scenario(client: redis.Redis, player_id: str, scenario_id: str | None) -> None:
    key = f"wos:player:{player_id}:scenario"
    if scenario_id is None or scenario_id == "":
        client.delete(key)
    else:
        client.set(key, scenario_id)


@dataclass(frozen=True)
class ScenarioRedisPurgeResult:
    """Counts from ``purge_scenarios_from_redis`` (sync UI helper)."""

    player_overrides_cleared: int = 0
    queue_items_removed: int = 0
    push_ttl_deleted: int = 0
    claims_deleted: int = 0
    recent_runs_pruned: int = 0
    instance_state_cleared: int = 0


def _queue_payload_matches_scenario_ids(
    data: dict[str, Any], scenario_ids: set[str]
) -> bool:
    for field in ("task_type", "dsl_scenario", "scenario"):
        val = str(data.get(field) or "").strip()
        if val and val in scenario_ids:
            return True
    return False


def _is_pending_queue_zset_key(client: redis.Redis, key: str) -> bool:
    if not key.startswith("wos:queue:"):
        return False
    if ":history" in key or ":running" in key or ":idx:" in key:
        return False
    try:
        return str(client.type(key) or "").lower() == "zset"
    except redis.RedisError:
        return False


def _running_task_types(client: redis.Redis, instance_id: str) -> set[str]:
    """Task types currently marked running for an instance (if any)."""
    out: set[str] = set()
    key = f"wos:queue:running:{instance_id}"
    try:
        raw = _r_get(client, key)
    except redis.RedisError:
        return out
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(data, dict):
        return out
    for field in ("task_type", "dsl_scenario", "scenario"):
        val = str(data.get(field) or "").strip()
        if val:
            out.add(val)
    return out


def purge_scenarios_from_redis(
    client: redis.Redis,
    *,
    scenario_ids: set[str],
    player_ids: list[str],
    instance_ids: list[str],
) -> ScenarioRedisPurgeResult:
    """Drop Redis artefacts for disabled scenario keys (queue, overrides, TTL, …).

    Matches queue payloads on ``task_type``, ``dsl_scenario``, and ``scenario``.
    Does not touch ``wos:queue:history:*`` or in-flight ``wos:queue:running:*``.
  """
    if not scenario_ids:
        return ScenarioRedisPurgeResult()

    player_overrides_cleared = 0
    for pid in player_ids:
        key = f"wos:player:{pid}:scenario"
        try:
            raw = _r_get(client, key)
        except redis.RedisError:
            continue
        if raw is None:
            continue
        val = raw.decode() if isinstance(raw, bytes) else str(raw)
        if val.strip() in scenario_ids:
            with suppress(redis.RedisError):
                if client.delete(key):
                    player_overrides_cleared += 1

    queue_items_removed = 0
    for key in client.scan_iter("wos:queue:*"):
        ks = str(key)
        if not _is_pending_queue_zset_key(client, ks):
            continue
        try:
            payloads = _r_zrange(client, ks, 0, -1)
        except redis.RedisError:
            continue
        for payload in payloads:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if _queue_payload_matches_scenario_ids(data, scenario_ids):
                with suppress(redis.RedisError):
                    if client.zrem(ks, payload):
                        queue_items_removed += 1

    push_ttl_deleted = 0
    for key in client.scan_iter("wos:*:push_ttl:*"):
        ks = str(key)
        suffix = ks.rsplit(":", 1)[-1]
        if suffix in scenario_ids:
            with suppress(redis.RedisError):
                if client.delete(ks):
                    push_ttl_deleted += 1

    claims_deleted = 0
    for sid in scenario_ids:
        with suppress(redis.RedisError):
            if client.delete(f"wos:claimed:{sid}"):
                claims_deleted += 1

    recent_runs_pruned = 0
    for iid in instance_ids:
        rr_key = f"wos:instance:{iid}:recent_runs"
        try:
            members = _r_zrange(client, rr_key, 0, -1)
        except redis.RedisError:
            continue
        for member in members:
            ms = member.decode() if isinstance(member, bytes) else str(member)
            task_type = ms.split("|", 1)[0].strip()
            if task_type in scenario_ids:
                with suppress(redis.RedisError):
                    if client.zrem(rr_key, member):
                        recent_runs_pruned += 1

    instance_state_cleared = 0
    clear_mapping = {
        "current_scenario": "",
        "current_task_type": "",
        "current_task_id": "",
        "current_task_player": "",
        "current_task_started_at": "",
        "current_task_region": "",
        "last_active_scenario": "",
        "last_active_scenario_priority": "",
        "last_active_scenario_player": "",
        "last_active_scenario_step": "",
        "last_active_scenario_trace": "",
    }
    for iid in instance_ids:
        running_types = _running_task_types(client, iid)
        if running_types & scenario_ids:
            continue
        state_key = f"wos:instance:{iid}:state"
        try:
            state = _r_hgetall(client, state_key) or {}
        except redis.RedisError:
            continue
        if not state:
            continue
        # All Redis clients in this module use ``decode_responses=True``, so
        # values are always ``str``. The legacy ``state.get(b"...")`` arms
        # were a defensive bytes-fallback that ``ty`` rightly flags as
        # ``invalid-argument-type`` against the typed dict; dropped.
        cur_scenario = str(state.get("current_scenario") or "").strip()
        cur_task = str(state.get("current_task_type") or "").strip()
        if cur_scenario not in scenario_ids and cur_task not in scenario_ids:
            continue
        with suppress(redis.RedisError):
            client.hset(state_key, mapping=clear_mapping)
            instance_state_cleared += 1

    return ScenarioRedisPurgeResult(
        player_overrides_cleared=player_overrides_cleared,
        queue_items_removed=queue_items_removed,
        push_ttl_deleted=push_ttl_deleted,
        claims_deleted=claims_deleted,
        recent_runs_pruned=recent_runs_pruned,
        instance_state_cleared=instance_state_cleared,
    )


def format_scenario_redis_purge_result(purge: ScenarioRedisPurgeResult) -> str:
    """Human-readable summary for Streamlit banners."""
    parts: list[str] = []
    if purge.player_overrides_cleared:
        parts.append(
            f"**{purge.player_overrides_cleared}** player override(s) "
            "(`wos:player:*:scenario`)"
        )
    if purge.queue_items_removed:
        parts.append(f"**{purge.queue_items_removed}** queued task(s) removed")
    if purge.push_ttl_deleted:
        parts.append(f"**{purge.push_ttl_deleted}** push TTL key(s) deleted")
    if purge.claims_deleted:
        parts.append(f"**{purge.claims_deleted}** cooperative claim(s) cleared")
    if purge.recent_runs_pruned:
        parts.append(f"**{purge.recent_runs_pruned}** recent-run marker(s) pruned")
    if purge.instance_state_cleared:
        parts.append(
            f"**{purge.instance_state_cleared}** instance state hash(es) cleared"
        )
    if not parts:
        return "Redis: nothing to remove for these scenario key(s)."
    return "Redis: " + "; ".join(parts) + "."


def dsl_preempt_gen_key(instance_id: str) -> str:
    """Redis key: monotonic counter bumped when debug UI enqueues \"Run scenario now\"."""

    return f"wos:instance:{instance_id}:dsl_preempt_gen"


def bump_dsl_preempt_generation(client: redis.Redis, instance_id: str) -> int:
    """Increment so any running DSL task can cooperatively exit before the next queue pop."""

    return _r_incr(client, dsl_preempt_gen_key(instance_id))


def push_instance_command(client: redis.Redis, instance_id: str, cmd: dict[str, Any]) -> None:
    client.lpush(f"wos:ui:command:{instance_id}", json.dumps(cmd))


def push_scheduler_command(client: redis.Redis, cmd: dict[str, Any]) -> None:
    client.lpush("wos:ui:command:scheduler", json.dumps(cmd))

