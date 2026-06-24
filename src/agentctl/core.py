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
    "drive",
    "history",
    "instance_state",
    "list_instances",
    "logs",
    "pause",
    "planners",
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
    "why",
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
# Explainability — why is this task running / what is each planner doing
# --------------------------------------------------------------------------- #
# A task's provenance is encoded in its task_id prefix (the scheduler stamps no
# explicit source field). Map prefix → human source so `why` can name who chose
# the running task. See src/scheduler/queue.py + the enqueue sites.
_SOURCE_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("coord-switch:", "coord_switch", "координатор: смена аккаунта"),
    ("coord:", "coordinator", "координатор (кампания/march)"),
    ("cron:", "cron", "плановый крон"),
    ("ovl:", "overlay", "overlay-правило (red-dot/иконка на экране)"),
    ("notify:", "notify", "уведомление телефона"),
    ("optimizer:", "optimizer", "автономный оптимизатор (hero/building)"),
    ("dsl:push:", "dsl_push", "push_scenario из другого сценария"),
    ("dsl:", "dsl_push", "push_scenario из другого сценария"),
    ("queue:", "operator", "оператор / API (Run now / календарь)"),
)


def _decode_source(task_id: str, *, priority: int, focused: bool) -> dict[str, str]:
    """Human source of a task from its id prefix (+ focus/priority hints)."""
    tid = str(task_id or "")
    code, label = "unknown", "неизвестно (нет префикса)"
    for prefix, c, lbl in _SOURCE_PREFIXES:
        if tid.startswith(prefix):
            code, label = c, lbl
            break
    if focused:
        return {"code": "focus", "label": f"focus-режим (поверх: {label})"}
    if code == "operator" and priority >= 90_000:
        return {"code": "focus", "label": "focus-режим (high-priority enqueue)"}
    return {"code": code, "label": label}


def _latest_zset_json(client: redis.Redis, key: str) -> dict[str, Any] | None:
    """Newest member of a decision-trace ZSET, parsed from JSON (or ``None``)."""
    if not key:
        return None
    try:
        items = client.zrevrange(key, 0, 0, withscores=True)
    except Exception:
        return None
    if not items:
        return None
    member, score = items[0]
    if isinstance(member, bytes):
        member = member.decode("utf-8", "replace")
    try:
        data = json.loads(member)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict):
        data.setdefault("ts", score)
        return data
    return None


def _resolve_active_fid(client: redis.Redis, instance_id: str | None = None) -> str:
    """First non-empty active player across instances (or one instance)."""
    from dashboard.redis_client import get_instance_state

    ids = [instance_id] if instance_id else list_instances()
    for iid in ids:
        if not iid:
            continue
        st = get_instance_state(client, iid)
        ap = (st.get("active_player") or st.get("current_task_player") or "").strip()
        if ap:
            return ap
    return ""


def _player_flat(client: redis.Redis, fid: str) -> dict[str, str]:
    """Merge durable SQLite state + the Redis player hash for presence checks.

    Durable carries ``buildings.levels.*`` etc; the Redis hash carries hot runtime
    values like ``stamina``. Best-effort — missing/unknown player yields ``{}``.
    """
    flat: dict[str, str] = {}
    fid = str(fid or "").strip()
    if not fid:
        return flat
    try:
        from config.state_store import get_state_store

        store = get_state_store().get(fid)
        if store is not None:
            flat.update({str(k): _cell(v) for k, v in store.to_flat_dict().items()})
    except Exception:
        pass
    try:
        from dashboard.redis_client import get_player_state_hash

        for k, v in get_player_state_hash(client, fid).items():
            if str(v).strip():
                flat[str(k)] = str(v)
    except Exception:
        pass
    return flat


def _cell(v: Any) -> str:
    return "" if v is None else str(v)


def _input_present(flat: dict[str, str], pattern: str) -> bool:
    """Is ``pattern`` satisfied by a non-empty value in ``flat``?

    ``foo.bar.*`` matches any key under ``foo.bar``; a plain key matches exactly.
    """
    pat = str(pattern or "").strip()
    if not pat:
        return True
    if pat.endswith(".*"):
        prefix = pat[:-2]
        return any(
            (k == prefix or k.startswith(prefix + ".")) and str(v).strip()
            for k, v in flat.items()
        )
    return bool(str(flat.get(pat, "")).strip())


def _load_planner_manifest(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Parse the per-game planner registry (``games/wos/planners.yaml``)."""
    from pathlib import Path as _Path

    import yaml

    from config.paths import repo_root

    p = _Path(path) if path else repo_root() / "games" / "wos" / "planners.yaml"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read planner manifest {p}: {exc}"
        raise AgentctlError(msg) from exc
    out = data.get("planners") if isinstance(data, dict) else None
    return [e for e in (out or []) if isinstance(e, dict)]


def _yaml_enabled(config_path: str, enabled_key: str) -> bool | None:
    """Read the on/off flag from a planner's config YAML (``None`` if no config)."""
    cfg = str(config_path or "").strip()
    if not cfg:
        return None
    import yaml

    from config.paths import repo_root

    try:
        data = yaml.safe_load((repo_root() / cfg).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return bool(data.get(enabled_key or "enabled", True))


def planners(fid: str | None = None, *, instance: str | None = None) -> dict[str, Any]:
    """Live status of every planner: LIVE / DORMANT / CALC-ONLY / VIA-MARCH (+ blind?).

    Reads the per-game registry (``games/wos/planners.yaml``), each planner's
    ``enabled`` flag, whether its required state inputs are observed for ``fid``
    (else *blind* — the reader is missing), and the latest decision-trace entry.
    Pure presenter: only YAML + Redis + SQLite, no planner code imported.
    """
    client = _redis()
    fid = str(fid or "").strip()
    fid_source = "arg" if fid else "none"
    if not fid:
        fid = _resolve_active_fid(client, instance)
        fid_source = "resolved" if fid else "none"
    flat = _player_flat(client, fid) if fid else {}

    rows: list[dict[str, Any]] = []
    for e in _load_planner_manifest():
        wired = str(e.get("wired", "")).strip()
        enabled = _yaml_enabled(str(e.get("config", "")), str(e.get("enabled_key", "enabled")))
        if wired == "calculator":
            status = "CALC-ONLY"
        elif wired == "via-march":
            status = "VIA-MARCH"
        elif enabled is False:
            status = "DORMANT"
        else:
            status = "LIVE"

        inputs = [str(x) for x in (e.get("observed_inputs") or [])]
        if not inputs:
            missing, blind = [], False
        elif not fid:
            missing, blind = inputs, None  # unknown without a player
        else:
            missing = [p for p in inputs if not _input_present(flat, p)]
            blind = bool(missing)

        trace_key = str(e.get("trace_key", "")).strip().replace("{fid}", fid)
        last = _latest_zset_json(client, trace_key) if (trace_key and fid) else None
        last_decision = None
        if isinstance(last, dict):
            last_decision = {
                "ts": last.get("ts"),
                "action": last.get("action"),
                "reason": last.get("reason"),
                "target": last.get("target"),
            }

        rows.append(
            {
                "name": str(e.get("name", "")),
                "title": str(e.get("title", "")),
                "domain": str(e.get("domain", "")),
                "wired": wired,
                "status": status,
                "enabled": enabled,
                "blind": blind,
                "missing_inputs": missing,
                "last_decision": last_decision,
                "note": str(e.get("note", "")),
            }
        )

    return {"fid": fid, "fid_source": fid_source, "count": len(rows), "planners": rows}


def why(instance: str | None = None) -> dict[str, Any]:
    """Explain the running (or last) task: what it is, who chose it, the reasoning.

    Combines three signals already in Redis: the running-task record (scenario,
    priority, started), the task-id prefix decoded to a human *source*, the
    winning *rank_meta* (persisted by the worker at pop time, when present), and
    the latest autonomous decision from each wired planner for the player.
    """
    from dashboard.redis_client import (
        fetch_queue_history_rows,
        fetch_running_queue_row,
        get_instance_state,
    )

    iid = resolve_instance(instance)
    client = _redis()
    st = get_instance_state(client, iid)
    row = fetch_running_queue_row(client, instance_id=iid)

    from_history = False
    if row is not None:
        task_id = row.task_id
        payload = row.payload or {}
        scenario = payload.get("dsl_scenario") or row.task_type
        player_id = row.player_id
        priority = int(row.priority or 0)
        started_at = row.started_at or None
        region = row.region
        running = True
    else:
        # Nothing in flight — explain the most recent task instead (idle bot).
        hist = fetch_queue_history_rows(client, instance_id=iid, limit=1)
        h = hist[0] if hist else None
        from_history = h is not None
        payload = (getattr(h, "payload", None) or {}) if h else {}
        task_id = getattr(h, "task_id", "") if h else ""
        scenario = (getattr(h, "scenario", "") or getattr(h, "task_type", "")) if h else ""
        player_id = getattr(h, "player_id", "") if h else ""
        priority = int(getattr(h, "priority", 0) or payload.get("priority") or 0) if h else 0
        started_at = getattr(h, "started_at", None) if h else None
        region = (str(payload.get("region") or "").strip() or None) if h else None
        running = False

    focused = bool((st.get("focus_scenario") or "").strip())
    source = _decode_source(task_id, priority=priority, focused=focused)

    rank_meta = payload.get("rank_meta")
    if isinstance(rank_meta, str):
        try:
            rank_meta = json.loads(rank_meta)
        except json.JSONDecodeError:
            rank_meta = None

    # Latest autonomous decision per wired planner, for the task's player (or the
    # instance's active player when the task is device-level / has no player).
    dec_fid = str(player_id or "").strip() or _resolve_active_fid(client, iid)
    decisions: dict[str, Any] = {}
    if dec_fid:
        for dom in ("march", "stamina", "resource"):
            decisions[dom] = _latest_zset_json(client, f"wos:player:{dec_fid}:{dom}_decisions")

    return {
        "instance_id": iid,
        "running": running,
        "from_history": from_history,
        "task_id": task_id,
        "scenario": scenario,
        "player_id": player_id,
        "priority": priority,
        "started_at": started_at,
        "region": region,
        "focus": focused,
        "source": source,
        "rank_meta": rank_meta if isinstance(rank_meta, dict) else None,
        "decisions_player": dec_fid,
        "decisions": decisions,
    }


# --------------------------------------------------------------------------- #
# Drive — run ONE scenario on a device synchronously, in-process (dev velocity)
# --------------------------------------------------------------------------- #
# Noisy instance-state fields that churn every tick — dropped from the state diff
# so `drive` surfaces the scenario's real effect (the keys a reader wrote), not
# heartbeat/timestamp noise.
_DIFF_NOISE = (
    "inst:last_active_scenario_trace",
    "inst:last_seen_at",
    "inst:uptime",
    "inst:current_task_started_at",
    "inst:dsl_last_",
    "inst:last_overlay_",
)

# Epoch-timestamp fields (``*.synced_at``, ``*.level_at``, ``*.detail_seen_at`` …)
# churn on every read regardless of whether the value changed — drop them so the
# diff highlights the actual reader output, not the heartbeat of having run.
_DIFF_NOISE_SUFFIX = ("_at", "_ts")


def _state_snapshot(client: redis.Redis, instance_id: str, fid: str) -> dict[str, str]:
    """Flat snapshot for before/after diffing: the Redis instance hash (``inst:``),
    the Redis player hash (``player:``) and the **durable SQLite player state**
    (``db:``). Readers persist to SQLite (``heroes.entries.*``, ``buildings.levels.*``,
    …) which the Redis hashes don't mirror — including it lets ``drive`` surface a
    reader's real output without a separate ``botctl player``.
    """
    from dashboard.redis_client import get_instance_state, get_player_state_hash

    snap = {f"inst:{k}": v for k, v in get_instance_state(client, instance_id).items()}
    if fid:
        snap.update({f"player:{k}": v for k, v in get_player_state_hash(client, fid).items()})
        try:
            from config.state_store import get_state_store

            store = get_state_store().get(fid)
            if store is not None:
                snap.update({f"db:{k}": _cell(v) for k, v in store.to_flat_dict().items()})
        except Exception:
            pass
    return snap


def _state_diff(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    """Keys whose value changed (added/removed/edited), minus heartbeat noise."""
    out: dict[str, Any] = {}
    for k in set(before) | set(after):
        if before.get(k) == after.get(k):
            continue
        if any(k.startswith(n) for n in _DIFF_NOISE):
            continue
        if k.endswith(_DIFF_NOISE_SUFFIX):
            continue
        out[k] = {"before": before.get(k), "after": after.get(k)}
    return out


async def _drive_async(instance_id: str, scenario: str, fid: str, timeout: float) -> Any:
    """Bootstrap a headless runtime, run the scenario task, return its TaskResult."""
    import asyncio

    import redis.asyncio as aioredis

    from config.loader import load_settings
    from config.runtime_bootstrap import bootstrap_runtime_observability

    settings = load_settings()
    try:
        bootstrap_runtime_observability("botctl-drive", instance_id=instance_id)
    except Exception:
        pass  # observability is best-effort; never block a drive on it

    # Bytes-mode async client matching the worker (aioredis.from_url without
    # decode_responses) so the DSL task's hget/hset behave identically.
    ar = aioredis.from_url(settings.redis.url, socket_connect_timeout=5.0)
    serial = _serial_for_instance(instance_id)
    try:
        from tasks.dsl_scenario import DslScenarioTask

        task = DslScenarioTask(
            task_id=f"drive:{instance_id}:{int(time.time())}",
            player_id=fid,
            scenario_key=scenario,
            redis_client=ar,
        )
        return await asyncio.wait_for(task.execute(instance_id), timeout=float(timeout))
    finally:
        try:
            await ar.aclose()
        except Exception:
            pass
        if serial:
            try:
                from adb.scrcpy import close_scrcpy_client

                close_scrcpy_client(serial)
            except Exception:
                pass


def drive(
    scenario: str,
    instance: str | None = None,
    *,
    player_id: str = "",
    approval: bool = True,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run ONE scenario on a device **synchronously, in-process** — return its
    step trace + the resulting state diff.

    Unlike ``run_scenario``/focus (which enqueue and spawn a background worker
    that the orphan-watchdog later kills), this constructs the ``DslScenarioTask``
    and awaits ``execute`` directly: no scheduler, no worker, no orphaned process.
    The scenario self-routes to its ``node:`` first, then runs its steps.

    ``approval=False`` bypasses the click-approval gate for this run (restored
    after). Requires NO worker running on the instance — scrcpy is a single holder
    per device, so a live worker would conflict.
    """
    import asyncio

    iid = resolve_instance(instance)
    scenario = str(scenario or "").strip()
    if not scenario:
        msg = "scenario key is required"
        raise AgentctlError(msg)

    client = _redis()
    fid = str(player_id or "").strip()
    before = _state_snapshot(client, iid, fid)

    flag_key = f"wos:ui:click_approval:enabled:{iid}"
    prior_flag = client.get(flag_key)
    started = time.time()
    try:
        if not approval:
            client.set(flag_key, "0")
        result = asyncio.run(_drive_async(iid, scenario, fid, timeout))
    except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
        raise AgentctlError(f"scenario {scenario!r} timed out after {timeout:.0f}s") from exc
    finally:
        if not approval:
            if prior_flag is None:
                client.delete(flag_key)
            else:
                client.set(flag_key, prior_flag)

    after = _state_snapshot(client, iid, fid)
    meta = dict(getattr(result, "metadata", {}) or {})
    return {
        "instance_id": iid,
        "scenario": scenario,
        "player_id": fid,
        "ok": bool(getattr(result, "success", False)),
        "completed": bool(meta.get("scenario_completed")),
        "reason": str(meta.get("reason") or ""),
        "duration_s": round(time.time() - started, 1),
        "approval_bypassed": not approval,
        "steps": meta.get("steps_trace") or [],
        "state_diff": _state_diff(before, after),
    }


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
    focus: bool = False,
) -> dict[str, Any]:
    """Enqueue a scenario to run on an instance.

    Delegates to :func:`api.services.queue_api.enqueue_user_task` (the same path
    the dashboard calendar uses): resolves the scenario, builds a TaskEnvelope,
    ZADDs it, and nudges the scheduler. ``when`` is a unix timestamp; default is
    *now*. Returns ``{task_id, queue_key, replaced, instance_id, scenario}``.

    With ``focus=True`` the instance is pinned to run ONLY this scenario — all
    autonomous work (crons, overlay pushes, identity probe) is suppressed and a
    worker is started if none is alive (see :mod:`api.services.focus`).
    """
    iid = resolve_instance(instance)
    scenario = str(scenario or "").strip()
    if not scenario:
        msg = "scenario key is required"
        raise AgentctlError(msg)
    client = _redis()
    if focus:
        from api.services.focus import focus_instance

        try:
            res = focus_instance(
                client,
                instance_id=iid,
                scenario_key=scenario,
                player_id=str(player_id or "").strip(),
                abort_running=abort_running,
            )
        except KeyError as exc:  # unknown scenario
            raise AgentctlError(str(exc).strip("'")) from exc
        except ValueError as exc:  # e.g. player required for account-level scenario
            raise AgentctlError(str(exc)) from exc
        return res

    from api.services.queue_api import enqueue_user_task

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


def set_focus(
    scenario: str,
    instance: str | None = None,
    *,
    player_id: str = "",
    abort_running: bool = False,
) -> dict[str, Any]:
    """Pin an instance to run ONLY ``scenario`` (alias for ``run_scenario(focus=True)``)."""
    return run_scenario(
        scenario,
        instance,
        player_id=player_id,
        abort_running=abort_running,
        focus=True,
    )


def clear_focus(instance: str | None = None, *, stop_worker: bool = False) -> dict[str, Any]:
    """Clear focus mode, returning the instance to normal autopilot.

    By default leaves the worker running (idle in autopilot); ``stop_worker``
    also terminates an isolated worker.
    """
    from api.services.focus import unfocus_instance

    iid = resolve_instance(instance)
    return unfocus_instance(_redis(), instance_id=iid, stop_worker=stop_worker)


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
