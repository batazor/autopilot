"""Fleet / overview helpers (Streamlit-free)."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

from config.devices import DeviceRegistry, load_devices
from config.loader import InstanceConfig, load_settings
from dashboard.redis_client import (
    count_claimed_slots,
    count_queue_tasks,
    get_instance_state,
)


def _format_elapsed(seconds: float) -> str:
    sec = int(max(0.0, seconds))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _elapsed_since(ts_str: str) -> str | None:
    ts_str = ts_str.strip()
    if not ts_str:
        return None
    try:
        return _format_elapsed(time.time() - float(ts_str))
    except ValueError:
        return None


def _is_recent(ts_str: str, *, max_age_s: float) -> bool:
    ts_str = ts_str.strip()
    if not ts_str:
        return False
    try:
        return (time.time() - float(ts_str)) <= max_age_s
    except ValueError:
        return False


def fleet_status(row: dict[str, str]) -> str:
    if not row:
        return "offline"
    if row.get("paused") == "1":
        return "paused"
    st_val = (row.get("state") or "").strip().lower()
    alive = _is_recent(row.get("last_seen_at") or "", max_age_s=10.0)
    if st_val in {"restarting", "crashed"}:
        return st_val
    if not (row.get("worker_started_at") or "").strip():
        return "starting"
    if alive:
        return "live"
    return "stale"


def fleet_task_label(row: dict[str, str]) -> str:
    st_val = (row.get("state") or "").strip().lower()
    started = (row.get("current_task_started_at") or "").strip()
    if st_val != "busy" and not started:
        return "—"
    name = (
        (row.get("current_scenario") or "").strip()
        or (row.get("current_task_type") or "").strip()
    )
    elapsed = _elapsed_since(started) if started else None
    if not name:
        return f"busy · {elapsed}" if elapsed else "busy"
    return f"{name} · {elapsed}" if elapsed else name


def fleet_alert(row: dict[str, str]) -> str:
    err = (row.get("last_error") or "").strip()
    blocked = (row.get("queue_blocked_reason") or "").strip()
    parts = [p for p in (err, blocked) if p]
    return " · ".join(parts) if parts else ""


def _format_age(unix_ts: object) -> str:
    try:
        ts = float(unix_ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if ts <= 0:
        return "—"
    delta = max(0.0, time.time() - ts)
    if delta < 1.0:
        return "just now"
    if delta < 60.0:
        return f"{int(delta)}s ago"
    if delta < 3600.0:
        return f"{int(delta // 60)}m ago"
    if delta < 86400.0:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def read_player_state(client: redis.Redis, pid: str) -> dict[str, str]:
    try:
        raw = client.hgetall(f"wos:player:{pid}:state") or {}
    except Exception:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else str(v)
        )
        for k, v in raw.items()
    }


def _player_sub_row(
    client: redis.Redis,
    instance_id: str,
    player_id: str,
    *,
    active_players: set[str],
    game: str,
) -> dict[str, Any]:
    state = read_player_state(client, player_id)
    ig_id = (state.get("player_id") or "").strip()
    conf_s = (state.get("player_id_confidence") or "").strip()
    try:
        conf_disp = f"{float(conf_s):.2f}" if conf_s else ""
    except ValueError:
        conf_disp = conf_s
    return {
        "id": f"{instance_id}:{player_id}",
        "who": player_id,
        "on_device": player_id in active_players,
        "nickname": (state.get("nickname") or "").strip() or "—",
        "in_game_id": ig_id or "—",
        "ocr_conf": conf_disp or "—",
        "ocr_age": _format_age(state.get("player_id_at") or 0.0),
        "stove": (state.get("stove_level") or "").strip() or "—",
        "kid": (state.get("kid") or "").strip() or "—",
        "century": _format_age(state.get("century_player_sync_at") or 0.0),
        # Per-profile game (`wos` / `kingshot` / …) — UI uses it to render the
        # right game icon next to the player chip.
        "game": (game or "wos"),
    }


def build_fleet_rows(
    client: redis.Redis,
    instances: list[InstanceConfig],
    db_registry: DeviceRegistry,
) -> list[dict[str, Any]]:
    by_name = {str(d.name): d for d in db_registry.devices}
    active_players: set[str] = set()
    inst_ids: list[str] = []
    states: list[dict[str, str]] = []
    for inst in instances:
        iid = str(getattr(inst, "instance_id", ""))
        inst_ids.append(iid)
        row = get_instance_state(client, iid)
        states.append(row)
        ap = (row.get("active_player") or "").strip()
        if ap and ap != "—":
            active_players.add(ap)

    rows: list[dict[str, Any]] = []
    for iid, row in zip(inst_ids, states, strict=True):
        sub_rows: list[dict[str, Any]] = []
        dev = by_name.get(iid)
        if dev is not None:
            # Iterate profiles directly so we can carry profile.game through
            # to the UI (all_gamers() flattens profiles and drops the field).
            for profile in dev.profiles:
                profile_game = profile.game or dev.game or "wos"
                for gamer in profile.gamers:
                    pid = str(gamer.id)
                    sub_rows.append(
                        _player_sub_row(
                            client,
                            iid,
                            pid,
                            active_players=active_players,
                            game=profile_game,
                        )
                    )
        rows.append(
            {
                "instance_id": iid,
                "status": fleet_status(row),
                "active_player": (row.get("active_player") or "").strip() or "—",
                "node": (row.get("current_screen") or "").strip() or "—",
                "task": fleet_task_label(row),
                "uptime": _elapsed_since(row.get("worker_started_at") or "") or "—",
                "alert": fleet_alert(row),
                "paused": row.get("paused") == "1",
                "players": sub_rows,
            }
        )
    return rows


def count_live_instances(
    client: redis.Redis,
    instances: list[InstanceConfig],
) -> tuple[int, int, int]:
    live = paused = busy = 0
    for inst in instances:
        iid = getattr(inst, "instance_id", "")
        row = get_instance_state(client, iid)
        if row.get("paused") == "1":
            paused += 1
        if fleet_status(row) == "live":
            live += 1
        if (row.get("state") or "").strip().lower() == "busy":
            busy += 1
    return live, paused, busy


def build_overview(client: redis.Redis) -> dict[str, Any]:
    settings = load_settings()
    instances = settings.instances
    db_registry = load_devices()
    n_inst = len(instances)
    q = count_queue_tasks(client)
    claimed = count_claimed_slots(client)
    live, paused, busy = count_live_instances(client, instances)
    fleet = build_fleet_rows(client, instances, db_registry) if instances else []
    return {
        "metrics": {
            "instances": n_inst,
            "live_workers": live,
            "queue": q,
            "busy": busy,
            "locks": claimed,
            "paused": paused,
        },
        "fleet": fleet,
        "has_devices": bool(db_registry.devices),
    }
