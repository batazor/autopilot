"""Fleet attention feed: one ranked list of problems that need an operator.

The dashboard used to scatter trouble across the UI — a ``stale`` pill on the
fleet table, an ``Approval pending`` chip in the sidebar, a nav-error bar on
the instance page, ``Overdue`` rows on the queue page — and left the operator
to reassemble the story. This module reads the same Redis state those widgets
read and emits a single list of actionable items, each tagged with the
instance it concerns and a ``kind`` the web client maps to a "go fix it" link.

Design rules:

* **Actionable or absent.** A manually paused instance is the operator's own
  doing and never listed. Consequences are suppressed when their cause is
  already listed (a paused/blocked instance accumulates overdue tasks — only
  the cause item shows).
* **No process introspection.** "Is the bot running?" is derived from worker
  heartbeats in Redis, so the same logic works for local dev and the Docker
  split (API and worker in different containers).
"""
from __future__ import annotations

import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from api.services import click_approval_store
from api.services.fleet import fleet_status
from config.loader import load_settings
from dashboard.load_failures import read_load_failures
from dashboard.redis_client import (
    fetch_next_queue_row_for_instance,
    get_instance_state,
)

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

# A queue head that has been due for less than this is normal scheduling
# jitter (worker mid-task, between pops). Beyond it the queue is stuck.
OVERDUE_STUCK_THRESHOLD_S = 30 * 60.0

_DEVICE_OFFLINE_ERROR = "device offline (ADB)"
_ADB_OFFLINE_EXHAUSTED_FIELD = "adb_offline_retry_exhausted"
_ADB_OFFLINE_ATTEMPTS_FIELD = "adb_offline_attempts"
_ADB_OFFLINE_RETRY_LIMIT = 5


def is_device_offline(state_row: dict[str, Any]) -> bool:
    """True when the instance's worker reports its ADB device unreachable.

    Shared with the queue view, which marks pending tasks of offline devices
    as blocked (they can never be picked up until the device reconnects).
    """
    last_error = (state_row.get("last_error") or "").strip()
    blocked = (state_row.get("queue_blocked_reason") or "").strip()
    paused = state_row.get("paused") == "1"
    auto_paused = state_row.get("auto_paused") == "1"
    return _DEVICE_OFFLINE_ERROR in last_error or _DEVICE_OFFLINE_ERROR in blocked or (
        auto_paused and paused and not last_error
    )


def _item(
    *,
    kind: str,
    severity: str,
    title: str,
    instance_id: str = "",
    detail: str = "",
    ts: float | None = None,
    dismissible: bool = False,
    debug_log: str = "",
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{instance_id}" if instance_id else f"{kind}",
        "kind": kind,
        "severity": severity,
        "instance_id": instance_id,
        "title": title,
        "detail": detail,
        "ts": ts,
        "dismissible": dismissible,
        "debug_log": debug_log,
    }


def _dismiss_key(kind: str, instance_id: str) -> str:
    return f"wos:attention:dismissed:{kind}:{instance_id}"


def _is_dismissed(client: redis.Redis, kind: str, instance_id: str) -> bool:
    try:
        return bool(client.get(_dismiss_key(kind, instance_id)))
    except Exception:
        return False


def _clear_dismissed(client: redis.Redis, kind: str, instance_id: str) -> None:
    with suppress(Exception):
        client.delete(_dismiss_key(kind, instance_id))


def dismiss_item(client: redis.Redis, *, kind: str, instance_id: str) -> bool:
    """Hide an attention item that is safe to treat as acknowledged.

    Currently only expected offline devices are dismissible. This does not
    change worker pause state or queue blocking; it only removes the nag from
    the attention feed until the device comes back online.
    """
    clean_kind = (kind or "").strip()
    clean_iid = (instance_id or "").strip()
    if clean_kind != "device_offline" or not clean_iid:
        return False
    try:
        client.set(_dismiss_key(clean_kind, clean_iid), str(time.time()))
    except Exception:
        return False
    return True


def _load_failure_items(client: redis.Redis) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for f in read_load_failures(client):
        subject = (
            str(f.get("file") or "").strip()
            or str(f.get("scenario") or "").strip()
            or str(f.get("source") or "").strip()
        )
        task = str(f.get("task") or "").strip()
        if task:
            subject = f"{subject} · {task}"
        ts_raw = f.get("ts")
        try:
            ts = float(ts_raw) if ts_raw is not None else None
        except (TypeError, ValueError):
            ts = None
        source = str(f.get("source") or "").strip()
        severity_raw = str(f.get("severity") or "").strip().lower()
        is_warning = severity_raw == SEVERITY_WARNING
        if source == "startup_validation":
            title_prefix = (
                "Startup config validation warning"
                if is_warning
                else "Startup config validation failed"
            )
            severity = SEVERITY_WARNING if is_warning else SEVERITY_CRITICAL
        else:
            title_prefix = "Scenario failed to load"
            severity = SEVERITY_CRITICAL
        items.append(
            _item(
                kind="load_failure",
                severity=severity,
                title=f"{title_prefix}: {subject}",
                detail=str(f.get("error") or "").strip(),
                ts=ts,
                debug_log=str(f.get("trace") or f.get("log") or "").strip(),
            )
        )
    # One stable id per failure row, not per kind — several files can break
    # at once and each needs its own line.
    for i, item in enumerate(items):
        item["id"] = f"load_failure:{i}"
    return items


def _approval_pending(client: redis.Redis, instance_id: str) -> bool:
    """Is a click approval waiting for the operator?

    This is *not* an attention item: a pending approval is normal in
    click-approval mode and is already surfaced in the bot control panel. We
    detect it here only to suppress the stuck-task item below, since a task
    parked on an approval would otherwise look like it is running past its
    timeout.
    """
    try:
        return click_approval_store.get_pending(client, instance_id) is not None
    except Exception:
        return False


def _overdue_item(
    client: redis.Redis, instance_id: str, *, now: float
) -> dict[str, Any] | None:
    row = fetch_next_queue_row_for_instance(client, instance_id=instance_id)
    if row is None:
        return None
    overdue_s = now - float(row.scheduled_at)
    if overdue_s < OVERDUE_STUCK_THRESHOLD_S:
        return None
    hours, rem = divmod(int(overdue_s), 3600)
    age = f"{hours}h {rem // 60}m" if hours else f"{rem // 60}m"
    return _item(
        kind="queue_stuck",
        severity=SEVERITY_WARNING,
        instance_id=instance_id,
        title=f"{instance_id} queue is not draining",
        detail=f"oldest task overdue for {age} ({row.task_type})",
    )


def _stuck_task_item(
    row: dict[str, str], instance_id: str, *, now: float
) -> dict[str, Any] | None:
    if (row.get("state") or "").strip().lower() != "busy":
        return None
    try:
        started = float((row.get("current_task_started_at") or "").strip())
    except ValueError:
        return None
    try:
        threshold = float(load_settings().worker.task_timeout_seconds)
    except Exception:
        return None
    elapsed = now - started
    if threshold <= 0 or elapsed < threshold:
        return None
    name = (
        (row.get("current_scenario") or "").strip()
        or (row.get("current_task_type") or "").strip()
        or "task"
    )
    hours, rem = divmod(int(elapsed), 3600)
    age = f"{hours}h {rem // 60}m" if hours else f"{rem // 60}m"
    return _item(
        kind="task_stuck",
        severity=SEVERITY_WARNING,
        instance_id=instance_id,
        title=f"{instance_id}: task running for {age}",
        detail=f"{name} exceeds the {int(threshold // 60)}m task timeout",
        ts=started,
    )


def _instance_items(
    client: redis.Redis, instance_id: str, *, now: float
) -> tuple[list[dict[str, Any]], bool]:
    """Items for one instance plus whether its worker is live."""
    items: list[dict[str, Any]] = []
    row = get_instance_state(client, instance_id)
    status = fleet_status(row)
    live = status == "live"
    paused = row.get("paused") == "1"
    last_error = (row.get("last_error") or "").strip()
    blocked = (row.get("queue_blocked_reason") or "").strip()
    nav_error = (row.get("nav_error") or "").strip()
    approval_pending = _approval_pending(client, instance_id)

    device_offline = is_device_offline(row)
    if device_offline:
        if not _is_dismissed(client, "device_offline", instance_id):
            retry_exhausted = row.get(_ADB_OFFLINE_EXHAUSTED_FIELD) == "1"
            attempts = (row.get(_ADB_OFFLINE_ATTEMPTS_FIELD) or "").strip()
            detail = "worker auto-paused; resumes when the device reconnects"
            if retry_exhausted:
                count = attempts or str(_ADB_OFFLINE_RETRY_LIMIT)
                detail = (
                    f"offline retry limit reached ({count}/{_ADB_OFFLINE_RETRY_LIMIT}); "
                    "worker is stopped until an operator resumes it"
                )
            items.append(
                _item(
                    kind="device_offline",
                    severity=SEVERITY_CRITICAL,
                    instance_id=instance_id,
                    title=f"{instance_id}: device offline (ADB)",
                    detail=detail,
                    dismissible=True,
                )
            )
    else:
        _clear_dismissed(client, "device_offline", instance_id)

    if (
        not device_offline
        and not approval_pending
        and status in {"stale", "crashed", "restarting"}
    ):
        items.append(
            _item(
                kind="worker_down",
                severity=SEVERITY_CRITICAL,
                instance_id=instance_id,
                title=f"{instance_id} worker is {status}",
                detail=last_error or blocked,
            )
        )
    elif not device_offline and (last_error or blocked):
        # Worker is alive but reporting trouble (game not ready, queue blocked).
        # Manual pause alone is the operator's own state — only surface it when
        # an error explains it.
        items.append(
            _item(
                kind="instance_error",
                severity=SEVERITY_WARNING,
                instance_id=instance_id,
                title=f"{instance_id}: {last_error or blocked}",
                detail=blocked if last_error and blocked else "",
            )
        )

    if live and not approval_pending:
        # A task running past the worker's own timeout. Only possible in
        # approval mode (the timeout is disabled there) or with zombie state.
        # While an approval is pending the wait is expected, so skip it.
        stuck = _stuck_task_item(row, instance_id, now=now)
        if stuck is not None:
            items.append(stuck)

    if nav_error:
        items.append(
            _item(
                kind="nav_error",
                severity=SEVERITY_WARNING,
                instance_id=instance_id,
                title=f"{instance_id}: navigation failing",
                detail=nav_error,
            )
        )

    # Overdue queue head only matters when the worker should be draining it.
    # Paused / down / offline instances already have their cause listed above.
    if live and not paused:
        overdue = _overdue_item(client, instance_id, now=now)
        if overdue is not None:
            items.append(overdue)

    return items, live


def _bot_process_running() -> bool:
    """Best-effort: is a local supervisor process alive?

    Only meaningful in local mode (API and worker share a host). In the
    Docker split this scan sees nothing and returns ``False``, which errs on
    the quiet side — the overview "Live workers 0/N" metric still carries the
    signal there.
    """
    try:
        from worker import local_bot

        return bool(local_bot.bot_status().get("running"))
    except Exception:
        return False


def build_attention_view(client: redis.Redis) -> dict[str, Any]:
    now = time.time()
    items: list[dict[str, Any]] = list(_load_failure_items(client))

    instance_ids = [
        str(getattr(inst, "instance_id", "")) for inst in load_settings().instances
    ]
    per_instance: list[dict[str, Any]] = []
    live_count = 0
    for iid in instance_ids:
        inst_items, live = _instance_items(client, iid, now=now)
        per_instance.extend(inst_items)
        live_count += int(live)

    if instance_ids and live_count == 0 and not _bot_process_running():
        # Every worker is down and no supervisor process is visible: that is
        # a deliberately stopped bot (instance state keeps reading "stale"
        # forever after a stop — worker_started_at is never cleared). The
        # sidebar bot control already owns the "start the bot" message, so
        # drop the worker_down noise and keep only causes that survive a
        # restart (approvals, device offline, broken YAML, …). When the
        # supervisor IS running with zero live workers, the per-instance
        # worker_down items are a real crash signal and stay.
        per_instance = [i for i in per_instance if i["kind"] != "worker_down"]
    items.extend(per_instance)

    items.sort(key=lambda i: (i["severity"] != SEVERITY_CRITICAL, i["instance_id"]))
    critical = sum(1 for i in items if i["severity"] == SEVERITY_CRITICAL)
    return {
        "items": items,
        "counts": {
            "critical": critical,
            "warning": len(items) - critical,
            "total": len(items),
        },
    }
