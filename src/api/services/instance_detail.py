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
