"""agentctl.core — single source of truth for agent-facing bot reads + control.

Every function returns plain, JSON-serialisable Python (dicts / lists / scalars)
and never prints. The ``botctl`` CLI and the MCP server are dumb presenters on
top of this module.

Design rules:

* **Headless.** Reads hit Redis / SQLite directly; control imports the existing
  service functions. Nothing here needs ``uv run api`` to be running.
* **Reuse, don't reinvent.** Each function delegates to a confirmed existing
  helper (``dashboard.redis_client``, ``api.services.*``, ``worker.local_bot``,
  ``config.state_store`` …). See the inline references.
* **Lazy imports.** Heavy modules are imported inside the functions that use
  them so ``import agentctl.core`` stays cheap and side-effect free.
* **Safety preserved.** Control functions only *enqueue work* or *send
  pause/resume/abort/restart*. They never tap the device — real taps still flow
  through the worker's click-approval gate.
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import redis

__all__ = [
    "AgentctlError",
    "abort",
    "bot_lifecycle",
    "devices",
    "history",
    "instance_state",
    "list_instances",
    "logs",
    "pause",
    "player",
    "queue",
    "queue_clear",
    "queue_remove",
    "queue_run_now",
    "resolve_instance",
    "resume",
    "run_scenario",
    "scenarios",
    "screenshot",
    "status",
    "trace",
]


class AgentctlError(Exception):
    """A user-facing failure (unknown instance, Redis down, …).

    The CLI prints ``str(exc)`` and exits non-zero; the MCP server returns it as
    an error payload. Use this for expected/operator errors, not bugs.
    """


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #
def _redis() -> redis.Redis:
    """Return a live sync Redis client or raise :class:`AgentctlError`."""
    from dashboard.redis_client import require_redis_connection

    try:
        return require_redis_connection()
    except Exception as exc:  # redis.RedisError and friends
        msg = f"Redis unreachable: {exc}"
        raise AgentctlError(msg) from exc


def _asdict(obj: Any) -> Any:
    """Best-effort JSON-friendly conversion (dataclass rows → dict)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def list_instances() -> list[str]:
    """All configured instance ids (device names), in config order."""
    from api.services.instances import list_instance_ids

    return list_instance_ids()


def resolve_instance(instance: str | None) -> str:
    """Validate ``instance``; default to the sole instance when unambiguous.

    Raises :class:`AgentctlError` with the list of choices when ``instance`` is
    missing-and-ambiguous or unknown — so the CLI/MCP can show a helpful error.
    """
    ids = list_instances()
    if instance:
        if instance not in ids:
            choices = ", ".join(ids) or "(none configured)"
            msg = f"unknown instance {instance!r}; known: {choices}"
            raise AgentctlError(msg)
        return instance
    if not ids:
        msg = "no instances configured"
        raise AgentctlError(msg)
    if len(ids) == 1:
        return ids[0]
    raise AgentctlError(
        "multiple instances configured — pass an instance id (one of: "
        + ", ".join(ids)
        + ")"
    )


# --------------------------------------------------------------------------- #
# Reads (side-effect free)
# --------------------------------------------------------------------------- #
def status() -> dict[str, Any]:
    """Fleet snapshot: ``{metrics, fleet, has_devices}``.

    Thin wrapper over :func:`api.services.fleet.build_overview` — the same data
    the dashboard Overview renders (per-instance status / screen / player /
    task / queue depth + fleet-wide counters).
    """
    from api.services.fleet import build_overview

    client = _redis()
    return build_overview(client)


def instance_state(instance: str | None = None) -> dict[str, Any]:
    """Full per-instance detail for one instance.

    Wrapper over :func:`api.services.instance_detail.build_instance_detail`,
    which already bundles status, paused, active player, current node/task,
    queue size, next-due, recent history and the raw Redis state hash.
    """
    from api.services.instance_detail import build_instance_detail

    iid = resolve_instance(instance)
    client = _redis()
    return build_instance_detail(client, iid)


def queue(instance: str | None = None, *, with_history: bool = False, limit: int = 20) -> dict[str, Any]:
    """Pending + running (+ optional history) for one instance."""
    from dashboard.redis_client import (
        count_queue_tasks_for_instance,
        fetch_queue_history_rows,
        fetch_queue_rows_for_instances,
        fetch_running_queue_row,
    )

    iid = resolve_instance(instance)
    client = _redis()
    pending = [_asdict(r) for r in fetch_queue_rows_for_instances(client, [iid])]
    pending.sort(key=lambda r: r.get("scheduled_at", 0.0))
    running = fetch_running_queue_row(client, instance_id=iid)
    out: dict[str, Any] = {
        "instance_id": iid,
        "queue_size": count_queue_tasks_for_instance(client, instance_id=iid),
        "running": _asdict(running) if running is not None else None,
        "pending": pending,
    }
    if with_history:
        out["history"] = [
            _asdict(h) for h in fetch_queue_history_rows(client, instance_id=iid, limit=limit)
        ]
    return out


def history(instance: str | None = None, *, limit: int = 20) -> dict[str, Any]:
    """Recent task execution records (newest first) for one instance."""
    from dashboard.redis_client import fetch_queue_history_rows

    iid = resolve_instance(instance)
    client = _redis()
    rows = fetch_queue_history_rows(client, instance_id=iid, limit=limit)
    return {"instance_id": iid, "history": [_asdict(h) for h in rows]}


def trace(instance: str | None = None) -> dict[str, Any]:
    """The most recent scenario step-by-step trace for one instance.

    Prefers the live ``last_active_scenario_trace`` hash field the executor
    persists on preempt/finish; falls back to the ``steps_trace`` carried on the
    newest history row. Returns ``{instance_id, source, scenario, steps}``.
    """
    from dashboard.redis_client import fetch_queue_history_rows, get_instance_state

    iid = resolve_instance(instance)
    client = _redis()
    row = get_instance_state(client, iid)

    raw = (row.get("last_active_scenario_trace") or "").strip()
    if raw:
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError:
            steps = None
        if isinstance(steps, list):
            return {
                "instance_id": iid,
                "source": "live",
                "scenario": (row.get("current_scenario") or row.get("last_active_scenario") or "").strip(),
                "steps": steps,
            }

    for h in fetch_queue_history_rows(client, instance_id=iid, limit=10):
        if h.steps_trace:
            return {
                "instance_id": iid,
                "source": "history",
                "scenario": h.scenario or h.task_type,
                "steps": h.steps_trace,
            }
    return {"instance_id": iid, "source": "none", "scenario": "", "steps": []}


def screenshot(instance: str | None = None, *, fresh: bool = False) -> dict[str, Any]:
    """Path to the current device preview PNG for one instance.

    Returns ``{instance_id, path, exists, mtime, age_s, captured, error}``. The
    caller (CLI/agent) then reads the PNG. With ``fresh=True`` a new ADB
    screencap is captured to the rolling-preview path first (best effort — falls
    back to the existing file if ADB is unavailable).
    """
    from dashboard.reference_preview import rolling_live_preview_path

    iid = resolve_instance(instance)
    path = rolling_live_preview_path(iid)
    out: dict[str, Any] = {"instance_id": iid, "path": str(path), "captured": False, "error": ""}

    if fresh:
        try:
            _capture_fresh(iid, path)
            out["captured"] = True
        except Exception as exc:
            out["error"] = f"fresh capture failed, returning last preview: {exc}"

    exists = path.is_file()
    out["exists"] = exists
    if exists:
        mtime = path.stat().st_mtime
        out["mtime"] = mtime
        out["age_s"] = round(time.time() - mtime, 1)
    else:
        out["mtime"] = None
        out["age_s"] = None
        if not out["error"]:
            out["error"] = "no preview captured yet (start the bot, or use --fresh)"
    return out


def _capture_fresh(instance_id: str, path: Path) -> None:
    """Capture one ADB screencap to ``path`` (raises on failure)."""
    from adb.screencap import adb_screencap_png
    from config.loader import load_settings

    settings = load_settings()
    adb_bin = (getattr(settings.worker, "adb_executable", "") or "adb").strip() or "adb"
    serial = _serial_for_instance(instance_id)
    png, err = adb_screencap_png(adb_bin, serial)
    if png is None:
        msg = err or "adb screencap returned no data"
        raise AgentctlError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _serial_for_instance(instance_id: str) -> str | None:
    """ADB serial for an instance name, or ``None`` if unknown."""
    from config.devices import load_devices

    for d in load_devices().devices:
        if str(d.name) == instance_id:
            return d.effective_serial or None
    return None


def player(fid: str, key: str | None = None) -> dict[str, Any]:
    """Per-account state from the encrypted SQLite store, flattened (dot keys).

    ``key`` filters to entries whose flat key equals or is prefixed by it
    (e.g. ``buildings.levels`` → all building levels). Returns
    ``{player_id, state}`` where ``state`` is a flat dict.
    """
    from config.state_store import get_state_store

    fid = str(fid or "").strip()
    if not fid:
        msg = "player id (fid) is required"
        raise AgentctlError(msg)
    try:
        store = get_state_store().get(fid)
    except Exception as exc:
        msg = f"could not open state store: {exc}"
        raise AgentctlError(msg) from exc
    if store is None:
        known = ", ".join(get_state_store().all_player_ids()) or "(none)"
        msg = f"unknown player {fid!r}; known: {known}"
        raise AgentctlError(msg)
    flat = store.to_flat_dict()
    if key:
        k = key.strip()
        flat = {fk: fv for fk, fv in flat.items() if fk == k or fk.startswith(k + ".")}
    return {"player_id": fid, "state": flat}


def scenarios(*, grep: str | None = None, module_scope: str = "all", game: str | None = None) -> dict[str, Any]:
    """List DSL scenarios + metadata (key, name, enabled, device_level, source).

    Wrapper over :func:`api.services.modules_api.list_scenarios`. ``grep``
    (case-insensitive) filters by key/name substring.
    """
    from api.services.modules_api import list_scenarios

    rows = list_scenarios(module_scope=module_scope, game=game)
    if grep:
        g = grep.lower()
        rows = [
            r
            for r in rows
            if g in str(r.get("key", "")).lower() or g in str(r.get("name", "")).lower()
        ]
    return {"count": len(rows), "scenarios": rows}


def devices() -> dict[str, Any]:
    """Configured devices + per-device backends + ADB-online status."""
    from config.devices import load_devices

    online = _online_serials()
    out: list[dict[str, Any]] = []
    for d in load_devices().devices:
        serial = d.effective_serial
        out.append(
            {
                "name": d.name,
                "adb_serial": serial,
                "screenshot_backend": (d.screenshot_backend or "(default)"),
                "input_backend": (d.input_backend or "(default)"),
                "online": (serial in online) if online is not None else None,
            }
        )
    return {"devices": out, "adb_online_known": online is not None}


def _online_serials() -> set[str] | None:
    """Serials in ``adb devices`` state ``device``; ``None`` if adb unusable."""
    import subprocess

    from config.loader import load_settings

    adb_bin = (getattr(load_settings().worker, "adb_executable", "") or "adb").strip() or "adb"
    try:
        proc = subprocess.run(
            [adb_bin, "devices"], capture_output=True, text=True, timeout=10
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    serials: set[str] = set()
    for line in proc.stdout.splitlines()[1:]:  # skip "List of devices attached"
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.add(parts[0])
    return serials


def logs(*, instance: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Tail a local worker logfile if one is configured.

    The worker logs to stdout by default (no logfile), so this reads
    ``$WOS_LOG_FILE`` or ``logs/bot.log`` under the repo when present, else
    points at the structured alternatives. ``instance`` filters lines that
    mention ``[<instance>/`` (the log context prefix) when a file is found.
    """
    import os
    from pathlib import Path

    from config.paths import repo_root

    candidates = []
    env_path = os.environ.get("WOS_LOG_FILE", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(repo_root() / "logs" / "bot.log")

    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return {
            "logfile": None,
            "lines": [],
            "hint": (
                "No local logfile. The worker logs to the console running "
                "`uv run play`/`uv run bot`. For structured recent activity use "
                "`botctl history`/`botctl trace`; prod ERROR+ is in Grafana Loki "
                "(query via the grafana MCP)."
            ),
        }
    try:
        text = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        msg = f"could not read {path}: {exc}"
        raise AgentctlError(msg) from exc
    if instance:
        needle = f"[{instance}/"
        text = [ln for ln in text if needle in ln]
    return {"logfile": str(path), "lines": text[-max(1, limit):]}


# --------------------------------------------------------------------------- #
# Control (real side effects)
# --------------------------------------------------------------------------- #
def run_scenario(
    scenario: str,
    instance: str | None = None,
    *,
    player_id: str = "",
    when: float | None = None,
    priority: int = 50_000,
    replace: bool = False,
    abort_running: bool = False,
) -> dict[str, Any]:
    """Enqueue a scenario to run on an instance.

    Delegates to :func:`api.services.queue_api.enqueue_user_task` (the same path
    the dashboard calendar uses): resolves the scenario, builds a TaskEnvelope,
    ZADDs it, and nudges the scheduler. ``when`` is a unix timestamp; default is
    *now*. Returns ``{task_id, queue_key, replaced, instance_id, scenario}``.
    """
    from api.services.queue_api import enqueue_user_task

    iid = resolve_instance(instance)
    scenario = str(scenario or "").strip()
    if not scenario:
        msg = "scenario key is required"
        raise AgentctlError(msg)
    client = _redis()
    try:
        res = enqueue_user_task(
            client,
            scenario_key=scenario,
            instance_id=iid,
            player_id=str(player_id or "").strip(),
            scheduled_at=float(when if when is not None else time.time()),
            priority=int(priority),
            replace_existing=replace,
            abort_running=abort_running,
        )
    except KeyError as exc:  # unknown scenario
        raise AgentctlError(str(exc).strip("'")) from exc
    except ValueError as exc:  # e.g. player required for account-level scenario
        raise AgentctlError(str(exc)) from exc
    return {**res, "instance_id": iid, "scenario": scenario}


def _send_command(instance: str | None, cmd: dict[str, Any]) -> dict[str, Any]:
    from dashboard.redis_client import push_instance_command

    iid = resolve_instance(instance)
    push_instance_command(_redis(), iid, cmd)
    return {"ok": True, "instance_id": iid, "command": cmd}


def pause(instance: str | None = None) -> dict[str, Any]:
    """Pause an instance (worker stops picking up tasks)."""
    return _send_command(instance, {"cmd": "pause"})


def resume(instance: str | None = None) -> dict[str, Any]:
    """Resume a paused instance."""
    return _send_command(instance, {"cmd": "resume"})


def abort(instance: str | None = None, *, restart: bool = False) -> dict[str, Any]:
    """Skip the in-flight task (over pubsub); optionally restart the game."""
    from api.services.instance_detail import abort_current_task

    iid = resolve_instance(instance)
    abort_current_task(_redis(), iid, reason="botctl abort", restart=restart)
    return {"ok": True, "instance_id": iid, "restart": restart}


def bot_lifecycle(action: str) -> dict[str, Any]:
    """``status`` | ``start`` | ``stop`` the local worker supervisor."""
    from worker import local_bot

    action = str(action or "").strip().lower()
    if action == "status":
        return local_bot.bot_status()
    if action == "start":
        return local_bot.start_supervisor_subprocess()
    if action == "stop":
        return local_bot.stop_local_bot()
    msg = f"unknown bot action {action!r}; use status|start|stop"
    raise AgentctlError(msg)


def queue_remove(task_id: str) -> dict[str, Any]:
    """Remove a pending task by id."""
    from dashboard.redis_client import remove_queue_task

    task_id = str(task_id or "").strip()
    if not task_id:
        msg = "task_id is required"
        raise AgentctlError(msg)
    removed = remove_queue_task(_redis(), task_id)
    return {"ok": removed, "task_id": task_id, "removed": removed}


def queue_run_now(task_id: str) -> dict[str, Any]:
    """Boost a pending task to run immediately."""
    from dashboard.redis_client import push_scheduler_command, run_queue_task_now

    task_id = str(task_id or "").strip()
    if not task_id:
        msg = "task_id is required"
        raise AgentctlError(msg)
    client = _redis()
    ok = run_queue_task_now(client, task_id)
    if ok:
        push_scheduler_command(client, {"cmd": "optimize_now"})
    return {"ok": ok, "task_id": task_id}


def queue_clear(instance: str | None = None, *, all_instances: bool = False) -> dict[str, Any]:
    """Clear pending tasks for one instance (default) or the whole fleet.

    Per-instance clear removes each pending row by id (history + the running
    task are untouched). ``all_instances=True`` delegates to
    :func:`dashboard.redis_client.clear_queue_tasks`.
    """
    from dashboard.redis_client import (
        clear_queue_tasks,
        fetch_queue_rows_for_instances,
        remove_queue_task,
    )

    client = _redis()
    if all_instances:
        removed = clear_queue_tasks(client)
        return {"ok": True, "scope": "all", "removed": removed}
    iid = resolve_instance(instance)
    rows = fetch_queue_rows_for_instances(client, [iid])
    removed = sum(1 for r in rows if remove_queue_task(client, r.task_id))
    return {"ok": True, "scope": iid, "removed": removed}
