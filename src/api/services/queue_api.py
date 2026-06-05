"""Queue data for the dashboard API."""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from api.services.instances import list_instance_ids
from config.paths import repo_root
from config.trace_links import tempo_trace_url
from dashboard.redis_client import (
    QueueHistoryRow,
    QueueRow,
    RunningQueueRow,
    fetch_queue_history_rows,
    fetch_queue_rows_for_instances,
    fetch_running_queue_row,
    get_instance_state,
    push_scheduler_command,
    remove_queue_task,
    reschedule_queue_task,
    run_queue_task_now,
    sort_queue_rows_by_execution_order,
)
from dsl import template_resolver as _tmpl
from dsl.registry import scenario_yaml_tree_fingerprint
from optimizer import enqueue_envelope
from optimizer.dispatcher import TaskEnvelope

_VIEW_CACHE_LOCK = threading.Lock()
_VIEW_CACHE_REVISION: str = ""
_VIEW_CACHE: dict[str, Any] | None = None


def _queue_key(instance_id: str) -> str:
    return f"wos:queue:{str(instance_id or '').strip() or 'unknown'}"


def _decode_queue_payload(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def remove_pending_scenario_tasks(
    client: redis.Redis,
    *,
    instance_id: str,
    scenario_key: str,
) -> int:
    """Remove pending tasks for one scenario/instance before queueing a fresh run."""
    iid = str(instance_id or "").strip()
    scenario = str(scenario_key or "").strip()
    if not iid or not scenario:
        return 0
    key = _queue_key(iid)
    removed = 0
    try:
        payloads = client.zrange(key, 0, -1)
    except Exception:
        return 0
    for raw in payloads:
        payload = _decode_queue_payload(raw)
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if str(data.get("instance_id") or "").strip() != iid:
            continue
        if str(data.get("dsl_scenario") or "").strip() != scenario:
            continue
        try:
            removed += int(client.zrem(key, raw))
        except Exception:
            continue
    return removed


def _rel_time(ts: float, now: float) -> str:
    delta = ts - now
    abs_s = abs(delta)
    if abs_s < 60:
        label = f"{int(abs_s)}s"
    elif abs_s < 3600:
        m, s = divmod(int(abs_s), 60)
        label = f"{m}m {s}s" if s else f"{m}m"
    else:
        h, rem = divmod(int(abs_s), 3600)
        label = f"{h}h {rem // 60}m" if rem else f"{h}h"
    return f"in {label}" if delta >= 0 else f"{label} ago"


def _scenario_label(scenario_key: str) -> str:
    key = str(scenario_key or "").strip()
    if not key:
        return ""
    root = repo_root()
    fp = scenario_yaml_tree_fingerprint(root)
    return _scenario_label_cached(fp, key)


def _scenario_label_from_cache(
    cache: dict[str, str],
    fp: tuple[str, tuple[tuple[str, int, int], ...]],
    scenario_key: str,
) -> str:
    key = str(scenario_key or "").strip()
    if not key:
        return ""
    cached = cache.get(key)
    if cached is not None:
        return cached
    label = _scenario_label_cached(fp, key)
    cache[key] = label
    return label


def _fast_scenario_label(scenario_key: str) -> str:
    key = str(scenario_key or "").strip()
    if not key:
        return ""
    return key.replace(".", ": ").replace("_", " ").title()


@lru_cache(maxsize=2048)
def _scenario_label_cached(
    fp: tuple[str, tuple[tuple[str, int, int], ...]],
    scenario_key: str,
) -> str:
    return _tmpl.display_name(repo_root(), scenario_key)


def _serialize_pending(
    row: QueueRow,
    now: float,
) -> dict[str, Any]:
    overdue = row.scheduled_at < now
    rel = _rel_time(row.scheduled_at, now)
    scheduled = f"overdue · {rel}" if overdue else rel
    return {
        "task_id": row.task_id,
        "scheduled": scheduled,
        "scheduled_at": row.scheduled_at,
        "overdue": overdue,
        "player_id": row.player_id or "(device)",
        "instance_id": row.instance_id,
        "scenario": _fast_scenario_label(row.task_type),
        "scenario_key": row.task_type,
        "region": row.region or "—",
        "priority": row.priority,
        "cooperative": row.cooperative,
        "payload": row.payload,
    }


def _serialize_running(
    instance_id: str,
    row: RunningQueueRow,
    inst_state: dict[str, str],
    now: float,
    label_cache: dict[str, str] | None = None,
    label_fp: tuple[str, tuple[tuple[str, int, int], ...]] | None = None,
) -> dict[str, Any]:
    active_scenario = str(inst_state.get("current_scenario") or "").strip()
    try:
        step_now = int(inst_state.get("last_active_scenario_step") or 0)
    except (TypeError, ValueError):
        step_now = 0
    scenario = (
        _scenario_label_from_cache(label_cache, label_fp, row.task_type)
        if label_cache is not None and label_fp is not None
        else _scenario_label(row.task_type)
    )
    active_scenario_label = (
        _scenario_label_from_cache(label_cache, label_fp, active_scenario)
        if active_scenario and label_cache is not None and label_fp is not None
        else (_scenario_label(active_scenario) if active_scenario else "")
    )
    return {
        "task_id": row.task_id,
        "instance_id": instance_id,
        "scenario": scenario,
        "scenario_key": row.task_type,
        "active_scenario": active_scenario,
        "active_scenario_label": active_scenario_label,
        "step": step_now,
        "player_id": row.player_id or "(device)",
        "region": row.region or "—",
        "priority": int(row.priority),
        "started": _rel_time(row.started_at, now) if row.started_at > 0 else "—",
        "nav_target": str(inst_state.get("nav_target") or "").strip(),
    }


def _serialize_history(row: QueueHistoryRow) -> dict[str, Any]:
    total = row.steps_total
    trace = row.steps_trace
    done_full = row.scenario_completed
    if total is None and not trace:
        steps = "—"
    else:
        n = len(trace) if trace else 0
        mid = f"{n}/{total}" if total is not None else str(n)
        if done_full is True:
            steps = f"{mid} · complete"
        elif done_full is False:
            steps = f"{mid} · partial"
        else:
            steps = mid
    return {
        "task_id": row.task_id,
        "scenario": row.scenario or row.task_type,
        "scenario_key": row.task_type,
        "player_id": row.player_id or "(device)",
        "instance_id": row.instance_id,
        "priority": row.priority,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "duration_s": row.duration_s,
        "success": row.success,
        "region": row.region or "—",
        "reason": row.reason or row.error or "",
        "steps": steps,
        "trace_id": row.trace_id,
        "tempo_trace_url": tempo_trace_url(row.trace_id),
        "steps_trace": trace or None,
    }


def build_queue_view(client: redis.Redis) -> dict[str, Any]:
    now = time.time()
    instance_ids = list_instance_ids()
    label_fp: tuple[str, tuple[tuple[str, int, int], ...]] | None = None
    label_cache: dict[str, str] = {}
    pending_rows = sort_queue_rows_by_execution_order(
        client,
        fetch_queue_rows_for_instances(client, instance_ids),
    )
    pending = [
        _serialize_pending(r, now)
        for r in pending_rows
    ]

    running: list[dict[str, Any]] = []
    for iid in instance_ids:
        r = fetch_running_queue_row(client, instance_id=iid)
        if r is None or not r.task_id:
            continue
        if label_fp is None:
            label_fp = scenario_yaml_tree_fingerprint(repo_root())
        inst_state = get_instance_state(client, iid)
        running.append(
            _serialize_running(
                iid,
                r,
                inst_state,
                now,
                label_cache=label_cache,
                label_fp=label_fp,
            )
        )

    history_rows: list[QueueHistoryRow] = []
    for iid in instance_ids:
        history_rows.extend(fetch_queue_history_rows(client, instance_id=iid, limit=30))
    history_rows.sort(key=lambda h: h.finished_at, reverse=True)
    history = [_serialize_history(h) for h in history_rows[:80]]

    return {
        "pending": pending,
        "running": running,
        "history": history,
        "pending_count": len(pending),
        "pending_overdue_count": sum(1 for row in pending if row.get("overdue")),
        "history_count": len(history),
    }


def get_cached_queue_view(revision: str) -> dict[str, Any] | None:
    rev = str(revision or "").strip()
    if not rev:
        return None
    with _VIEW_CACHE_LOCK:
        if rev != _VIEW_CACHE_REVISION or _VIEW_CACHE is None:
            return None
        # Shallow copy is enough: FastAPI only serializes the nested lists/dicts.
        # Avoid deepcopy here because this path is the hot O(1) page load.
        return dict(_VIEW_CACHE)


def store_cached_queue_view(revision: str, view: dict[str, Any]) -> None:
    rev = str(revision or "").strip()
    if not rev:
        return
    with _VIEW_CACHE_LOCK:
        global _VIEW_CACHE_REVISION, _VIEW_CACHE
        _VIEW_CACHE_REVISION = rev
        _VIEW_CACHE = dict(view)


def run_task_now(client: redis.Redis, task_id: str) -> bool:
    ok = run_queue_task_now(client, task_id)
    if ok:
        push_scheduler_command(client, {"cmd": "optimize_now"})
        from dashboard.dashboard_events import publish_dashboard_event

        publish_dashboard_event(client, topic="queue", reason="run_now")
    return ok


def remove_tasks(client: redis.Redis, task_ids: list[str]) -> int:
    removed = 0
    for tid in task_ids:
        if remove_queue_task(client, tid):
            removed += 1
    if removed:
        from dashboard.dashboard_events import publish_dashboard_event

        publish_dashboard_event(client, topic="queue", reason="remove")
    return removed


def reschedule_task(client: redis.Redis, task_id: str, scheduled_at: float) -> bool:
    ok = reschedule_queue_task(client, task_id, scheduled_at)
    if ok:
        push_scheduler_command(client, {"cmd": "optimize_now"})
        from dashboard.dashboard_events import publish_dashboard_event

        publish_dashboard_event(client, topic="queue", reason="reschedule")
    return ok


def enqueue_user_task(
    client: redis.Redis,
    *,
    scenario_key: str,
    instance_id: str,
    player_id: str,
    scheduled_at: float,
    priority: int = 50_000,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Enqueue an operator-created task from the calendar UI.

    Resolves the scenario via the template resolver, builds a TaskEnvelope,
    and pushes through the same ZADD path the optimizer uses so the worker
    picks it up via ``pop_due`` indistinguishably from auto-scheduled work.
    """
    resolved = _tmpl.resolve(repo_root(), scenario_key)
    if resolved is None:
        msg = f"unknown scenario: {scenario_key}"
        raise KeyError(msg)
    doc = _tmpl.load_doc(repo_root(), scenario_key)
    device_level = bool(doc and doc[1].get("device_level") is True)
    pid = (player_id or "").strip()
    if not device_level and not pid:
        msg = "player_id required for account-level scenarios"
        raise ValueError(msg)
    replaced = (
        remove_pending_scenario_tasks(
            client,
            instance_id=str(instance_id),
            scenario_key=scenario_key,
        )
        if replace_existing
        else 0
    )
    if replace_existing and scenario_key.startswith("dreamscape_memory"):
        with suppress(Exception):
            client.hdel(
                f"wos:instance:{instance_id}:state",
                "dreamscape_memory.solve_state",
            )
    env = TaskEnvelope(
        task_id=f"queue:{uuid.uuid4().hex[:12]}",
        task_type="dsl_scenario",
        player_id=pid,
        instance_id=str(instance_id),
        dsl_scenario=scenario_key,
        set_node="",
        region=None,
        priority=int(priority),
        run_at=float(scheduled_at),
    )
    qk = enqueue_envelope(env, client)
    push_scheduler_command(client, {"cmd": "optimize_now"})
    from dashboard.dashboard_events import publish_dashboard_event

    publish_dashboard_event(
        client, topic="queue", instance_id=instance_id, reason="enqueue"
    )
    return {"task_id": env.task_id, "queue_key": qk, "replaced": replaced}
