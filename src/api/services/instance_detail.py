"""Per-instance dashboard data and commands."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from api.services.fleet import fleet_alert, fleet_status, fleet_task_label
from api.services.instances import list_instance_ids
from config.devices import player_ids_for_device
from config.loader import InstanceConfig, load_settings
from config.paths import repo_root
from dashboard.redis_client import (
    count_queue_tasks_for_instance,
    fetch_next_queue_row_for_instance,
    fetch_queue_history_rows,
    get_instance_state,
    push_instance_command,
)
from dashboard.reference_preview import load_rolling_instance_preview, rolling_live_preview_path
from dashboard.scenario_keys import runnable_scenario_keys


def _find_instance_config(instance_id: str) -> InstanceConfig | None:
    for inst in load_settings().instances:
        if inst.instance_id == instance_id:
            return inst
    return None


def _current_task_fields(row: dict[str, str]) -> dict[str, Any]:
    """Structured view of the in-flight task (``fleet_task_label`` renders the
    same data as one string with a fetch-time elapsed; the instance page needs
    the raw start timestamp to run a live timer and a stuck check)."""
    st_val = (row.get("state") or "").strip().lower()
    started_s = (row.get("current_task_started_at") or "").strip()
    started: float | None = None
    if st_val == "busy" or started_s:
        try:
            started = float(started_s)
        except ValueError:
            started = None
    name = (
        (row.get("current_scenario") or "").strip()
        or (row.get("current_task_type") or "").strip()
    )
    return {
        "task_scenario": name if started is not None else "",
        "task_started_at": started,
    }


def abort_current_task(
    client: redis.Redis,
    instance_id: str,
    *,
    reason: str,
    restart: bool = False,
) -> None:
    """Operator "skip task": kill the in-flight task, optionally restart the game.

    The abort goes over pubsub (``wos:events:abort_task:<iid>``) because the
    command list only drains between tasks — too late for a task that is the
    problem. With ``restart`` a ``restart`` command is queued as well; it is
    picked up right after the abort frees the worker loop (same ordering the
    game-health watchdog uses).
    """
    if instance_id not in list_instance_ids():
        msg = f"unknown instance: {instance_id}"
        raise ValueError(msg)
    import json

    client.publish(
        f"wos:events:abort_task:{instance_id}",
        json.dumps({"reason": reason}),
    )
    if restart:
        push_instance_command(client, instance_id, {"cmd": "restart"})


def build_instance_detail(client: redis.Redis, instance_id: str) -> dict[str, Any]:
    inst_cfg = _find_instance_config(instance_id)
    if inst_cfg is None:
        msg = f"unknown instance: {instance_id}"
        raise ValueError(msg)

    row = get_instance_state(client, instance_id)
    queue_n = count_queue_tasks_for_instance(client, instance_id=instance_id)
    next_row = fetch_next_queue_row_for_instance(client, instance_id=instance_id)
    player_ids = player_ids_for_device(inst_cfg.bluestacks_window_title)
    nav_error = (row.get("nav_error") or "").strip()

    preview_path = rolling_live_preview_path(instance_id)
    preview_mtime: float | None = None
    if preview_path.is_file():
        preview_mtime = preview_path.stat().st_mtime

    next_due: dict[str, Any] | None = None
    if next_row is not None:
        next_due = {
            "task_id": next_row.task_id,
            "task_type": next_row.task_type,
            "scheduled_at": next_row.scheduled_at,
        }

    history = fetch_queue_history_rows(client, instance_id=instance_id, limit=50)
    hist_out = [
        {
            "player_id": h.player_id or "(device)",
            "scenario": h.scenario or h.task_type,
            "started_at": h.started_at,
            "duration_s": h.duration_s,
            "success": h.success,
            "detail": h.reason or h.error or h.task_id,
            "trace_id": h.trace_id or "",
        }
        for h in history
    ]

    return {
        "instance_id": instance_id,
        "status": fleet_status(row),
        "paused": row.get("paused") == "1",
        "active_player": (row.get("active_player") or "").strip() or "—",
        "node": (row.get("current_screen") or "").strip() or "—",
        "task": fleet_task_label(row),
        **_current_task_fields(row),
        # Stuck threshold: the worker's own task timeout. In approval mode the
        # timeout is disabled (the task may legitimately wait on an operator),
        # which is exactly when a task can run for hours unnoticed.
        "task_stuck_after_s": int(load_settings().worker.task_timeout_seconds),
        "alert": fleet_alert(row),
        "nav_error": nav_error,
        "queue_size": queue_n,
        "next_due": next_due,
        "player_ids": player_ids,
        "runnable_scenarios": list(runnable_scenario_keys(str(repo_root()))),
        "preview_available": preview_path.is_file(),
        "preview_mtime": preview_mtime,
        "history": hist_out,
        "test_module": (row.get("test_module") or "").strip(),
        "state": row,
    }


def load_preview_png(instance_id: str) -> tuple[bytes | None, float | None]:
    img_bytes, _, mtime = load_rolling_instance_preview(instance_id)
    return img_bytes, mtime


def push_command(client: redis.Redis, instance_id: str, cmd: dict[str, Any]) -> None:
    if instance_id not in list_instance_ids():
        msg = f"unknown instance: {instance_id}"
        raise ValueError(msg)
    push_instance_command(client, instance_id, cmd)
