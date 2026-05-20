"""Redis access for click approvals (no Streamlit)."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

from adb.approvals import click_approval_enabled
from api.services.click_approval_overlay import (
    build_overlays,
    image_dimensions,
    load_preview_bytes,
)
from config.paths import repo_root
from config.trace_links import tempo_trace_url
from config.w3c_traceparent import w3c_trace_id_hex
from dsl import template_resolver as _tmpl
from tasks.dsl_scenario_helpers import _dsl_step_summary
from ui.redis_client import fetch_running_queue_row, get_instance_state


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _as_float(value: Any) -> float:
    try:
        return float(_as_text(value))
    except (TypeError, ValueError):
        return 0.0


def scenario_display_name(scenario_key: str) -> str:
    return _tmpl.display_name(repo_root(), scenario_key)


def _scenario_step_summaries(scenario_key: str) -> tuple[str, ...]:
    """Top-level step summaries for ``scenario_key`` (template-aware)."""
    if not scenario_key:
        return ()
    loaded = _tmpl.load_doc(repo_root(), scenario_key)
    if loaded is None:
        return ()
    _path, raw = loaded
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return ()
    return tuple(_dsl_step_summary(s) for s in steps)


def build_scenario_progress(
    client: Any,
    instance_id: str,
    instance_state: dict[str, str],
) -> dict[str, Any]:
    """Live step progress for the scenario on this instance (Streamlit parity)."""
    active_scenario = _as_text(instance_state.get("current_scenario"))
    summaries = _scenario_step_summaries(active_scenario) if active_scenario else ()
    total = len(summaries)
    running = fetch_running_queue_row(client, instance_id=instance_id)
    busy = _as_text(instance_state.get("state")).lower() == "busy"
    current_task_type = _as_text(instance_state.get("current_task_type"))
    has_task = bool(_as_text(instance_state.get("current_task_id")))
    is_running = bool(
        active_scenario
        and (
            (
                running is not None
                and running.task_id
                and running.task_type == active_scenario
            )
            or (
                busy
                and has_task
                and (current_task_type == active_scenario or not current_task_type)
            )
        )
    )
    step_display = 0
    step_iter = 0
    if total > 0:
        try:
            step_now = int(instance_state.get("last_active_scenario_step") or 0)
        except (TypeError, ValueError):
            step_now = 0
        cap = (total - 1) if is_running else total
        step_display = max(0, min(step_now, cap))
        try:
            step_iter = int(instance_state.get("last_active_scenario_iter") or 0)
        except (TypeError, ValueError):
            step_iter = 0
    nav_target = _as_text(instance_state.get("nav_target")) if is_running else ""
    return {
        "scenario_key": active_scenario,
        "scenario_label": scenario_display_name(active_scenario) if active_scenario else "",
        "step_current": step_display,
        "step_total": total,
        "step_iter": step_iter,
        "is_running": is_running,
        "nav_target": nav_target,
        "step_summaries": list(summaries),
    }


def _current_key(instance_id: str) -> str:
    return f"wos:ui:click_approval:current:{instance_id}"


def _heartbeat_key(instance_id: str) -> str:
    return f"wos:ui:click_approval:heartbeat:{instance_id}"


def _enabled_key(instance_id: str) -> str:
    return f"wos:ui:click_approval:enabled:{instance_id}"


def touch_heartbeat(client: Any, instance_id: str) -> None:
    if click_approval_enabled(instance_id):
        client.set(_heartbeat_key(instance_id), str(time.time()), ex=5)


def set_approval_enabled(client: Any, instance_id: str, *, enabled: bool) -> None:
    """Toggle the approval-mode flag for an instance.

    The Streamlit page used to write ``1`` / ``0`` to ``wos:ui:click_approval:enabled:<id>``
    and ``delete`` the heartbeat key when turning OFF so the worker doesn't wait
    on a stale page. Mirror that here so the API drives the same Redis contract.
    """
    client.set(_enabled_key(instance_id), "1" if enabled else "0")
    if not enabled:
        client.delete(_heartbeat_key(instance_id))
    from ui.dashboard_events import publish_dashboard_event

    publish_dashboard_event(
        client,
        topic="approval",
        instance_id=instance_id,
        reason="enabled" if enabled else "disabled",
    )


def clear_pending(client: Any, instance_id: str) -> bool:
    """Cancel any in-flight approval for an instance without writing a decision.

    Streamlit's Reset block did this when restarting the bot — a "pending" approval
    is owned by a worker that may have died and would otherwise block the next run.
    Replies ``reject`` on the response key (worker treats it like a manual reject)
    then deletes ``current`` so the UI clears.
    """
    curr_key = _current_key(instance_id)
    raw = client.get(curr_key)
    if not raw:
        return False
    try:
        payload = json.loads(_as_text(raw))
    except json.JSONDecodeError:
        client.delete(curr_key)
        return True
    if isinstance(payload, dict):
        response_key = _as_text(payload.get("response_key"))
        if response_key:
            client.set(response_key, "reject", ex=120)
    client.delete(curr_key)
    from ui.dashboard_events import publish_dashboard_event

    publish_dashboard_event(client, topic="approval", instance_id=instance_id, reason="clear")
    return True


def reset_current_screen(client: Any, instance_id: str) -> None:
    """Clear ``current_screen`` in the per-instance state hash.

    Used when the operator wants to force the detector to re-classify from scratch
    (matches the "Reset node to none (unknown)" button on the Streamlit page).
    """
    client.hset(f"wos:instance:{instance_id}:state", "current_screen", "")


def clear_queue_all(client: Any) -> int:
    """Wipe pending task queues (``wos:queue:*``) but keep ``:running`` so the
    currently-executing task on each worker is not lost.

    Returns the number of Redis keys deleted.
    """
    removed = 0
    for key in client.scan_iter("wos:queue:*"):
        k = str(key)
        if ":running" in k:
            continue
        if client.delete(k):
            removed += 1
    if removed:
        from ui.dashboard_events import publish_dashboard_event

        publish_dashboard_event(client, topic="queue", reason="clear_all")
    return removed


def get_active_player_in_game_id(client: Any, instance_id: str) -> str:
    """OCR'd in-game ``player_id`` of the active bot account on the instance, or ``""``.

    Mirrors ``ui.views.click_approvals.chrome._active_player_in_game_id`` so the
    approvals card on the Next.js page can show the same identity caption.
    """
    row = get_instance_state(client, instance_id) or {}
    active = _as_text(row.get("active_player"))
    if not active:
        return ""
    try:
        raw = client.hget(f"wos:player:{active}:state", "player_id")
    except Exception:
        return ""
    return _as_text(raw)


def _is_stale_from_previous_worker(payload: dict[str, Any], row: dict[str, str]) -> bool:
    status = _as_text(payload.get("status")).lower()
    if status and status != "waiting":
        return False
    created_at = _as_float(payload.get("created_at"))
    worker_started_at = _as_float(row.get("worker_started_at"))
    return created_at > 0 and worker_started_at > 0 and created_at < worker_started_at


def _is_stale_for_live_owner(payload: dict[str, Any], row: dict[str, str]) -> bool:
    status = _as_text(payload.get("status")).lower()
    if status and status != "waiting":
        return False
    ctx0 = payload.get("context")
    if not isinstance(ctx0, dict):
        return False
    payload_task_id = _as_text(ctx0.get("current_task_id"))
    live_task_id = _as_text(row.get("current_task_id"))
    if payload_task_id and live_task_id:
        return payload_task_id != live_task_id
    payload_scenario = _as_text(ctx0.get("scenario"))
    live_scenario = _as_text(row.get("current_scenario"))
    if payload_scenario and live_scenario:
        return payload_scenario != live_scenario
    return False


def _is_stale_navigation_approval(payload: dict[str, Any], row: dict[str, str]) -> bool:
    status = _as_text(payload.get("status")).lower()
    if status and status != "waiting":
        return False
    ctx0 = payload.get("context")
    if not isinstance(ctx0, dict):
        return False
    if _as_text(ctx0.get("approval_source")).lower() != "navigation":
        return False
    approval_from = _as_text(ctx0.get("approval_from_screen"))
    live_screen = _as_text(row.get("current_screen"))
    return bool(approval_from and live_screen and approval_from != live_screen)


def clear_stale_pending(client: Any, instance_id: str, *, curr_key: str, raw: str) -> bool:
    try:
        payload = json.loads(_as_text(raw))
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    row = get_instance_state(client, instance_id)
    stale_after_restart = _is_stale_from_previous_worker(payload, row)
    stale_owner = _is_stale_for_live_owner(payload, row)
    stale_navigation = _is_stale_navigation_approval(payload, row)
    if not (stale_after_restart or stale_owner or stale_navigation):
        return False
    response_key = _as_text(payload.get("response_key"))
    if response_key:
        client.set(response_key, "reject")
    client.delete(curr_key)
    return True


def get_pending(client: Any, instance_id: str) -> dict[str, Any] | None:
    curr_key = _current_key(instance_id)
    raw = client.get(curr_key)
    if not raw:
        return None
    if clear_stale_pending(client, instance_id, curr_key=curr_key, raw=_as_text(raw)):
        return None
    try:
        payload = json.loads(_as_text(raw))
    except json.JSONDecodeError:
        client.delete(curr_key)
        return None
    if not isinstance(payload, dict):
        client.delete(curr_key)
        return None
    return payload


def _payload_action_label(payload: dict[str, Any]) -> str:
    """Human label for the operator's "Payload · …" expander header."""
    kind = _as_text(payload.get("type")).lower()
    if kind == "set_node":
        node = _as_text(payload.get("set_node"))
        return f"set node -> {node}" if node else "set node"
    if kind == "swipe":
        if _as_text(payload.get("gesture")).lower() == "long_press":
            return "long press"
        try:
            x1 = int(payload.get("x1") or 0)
            y1 = int(payload.get("y1") or 0)
            x2 = int(payload.get("x2") or 0)
            y2 = int(payload.get("y2") or 0)
            if x1 == x2 and y1 == y2:
                return "long press"
        except (TypeError, ValueError):
            pass
        return "swipe"
    if kind == "type_text":
        return "type text"
    if kind == "system_back":
        return "system back"
    if kind == "tap":
        return "click"
    if kind == "diagnostic":
        return "diagnostic"
    return kind or "action"


def _build_navigation_block(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract navigation route / hop info from a navigation-approval payload.

    Mirrors the Streamlit pending column: when ``approval_source == "navigation"``
    the worker stuffs ``approval_path`` (CSV of node names) and ``approval_hop_index``
    (1-based index of the hop being approved) into ``context``. The UI uses these to
    render the full route with the current edge highlighted.
    """
    ctx0 = payload.get("context")
    if not isinstance(ctx0, dict):
        return None
    src = _as_text(payload.get("approval_source")).lower() or _as_text(
        ctx0.get("approval_source")
    ).lower()
    if src != "navigation":
        return None
    nav_from = _as_text(ctx0.get("approval_from_screen"))
    nav_to = _as_text(ctx0.get("approval_to_screen"))
    path_csv = _as_text(ctx0.get("approval_path"))
    path_nodes = [s for s in path_csv.split(",") if s] if path_csv else []
    try:
        hop_index = int(_as_text(ctx0.get("approval_hop_index")))
    except ValueError:
        hop_index = 0
    return {
        "from": nav_from,
        "to": nav_to,
        "path": path_nodes,
        "hop_index": hop_index,
    }


def _build_task_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Threshold / score / text / confidence audit block from ``context``.

    The Streamlit page rendered these as a small ``"Overlay · threshold X · match
    score Y"`` caption under the region. Keep the same fields so the React side
    can render an equivalent.
    """
    ctx0 = payload.get("context")
    if not isinstance(ctx0, dict):
        return None
    threshold = _as_text(ctx0.get("current_task_threshold"))
    score = _as_text(ctx0.get("current_task_score"))
    text = _as_text(ctx0.get("current_task_text"))
    confidence = _as_text(ctx0.get("current_task_confidence"))
    if not any([threshold, score, text, confidence]):
        return None
    return {
        "threshold": threshold,
        "score": score,
        "text": text,
        "confidence": confidence,
    }


def _trace_id_from_payload(payload: dict[str, Any]) -> str:
    """Prefer explicit ``trace_id``, else derive from ``traceparent`` (W3C)."""
    direct = _as_text(payload.get("trace_id"))
    if direct:
        return direct
    return w3c_trace_id_hex(_as_text(payload.get("traceparent")) or None) or ""


def _labeling_href_for_region(
    client: Any,
    instance_id: str,
    region_name: str,
) -> str:
    """Next.js ``/labeling?…`` deep-link for an area.json region (Streamlit parity)."""
    from ui.views.click_approvals.common import (
        active_player_state_flat,
        labeling_query_params_for_area_region,
        load_area_doc,
    )

    reg = _as_text(region_name)
    if not reg:
        return ""
    area_path = repo_root() / "area.json"
    area_doc = load_area_doc(area_path)
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)
    qp = labeling_query_params_for_area_region(area_doc, reg, state_flat=state_flat)
    if not qp:
        return ""
    return f"/labeling?{urlencode(qp)}"


def get_approval_view(
    client: Any,
    instance_id: str,
    *,
    image_source: str = "capture",
) -> dict[str, Any]:
    touch_heartbeat(client, instance_id)
    enabled = click_approval_enabled(instance_id)
    instance_state = get_instance_state(client, instance_id)
    payload = get_pending(client, instance_id)
    scenario_key = ""
    scenario_label = ""
    region_label = ""
    action_type = ""
    action_label = ""
    set_node_target = ""
    trace_id = ""
    tempo_url = ""
    labeling_href = ""
    diagnostic_kind = ""
    diagnostic_attempts = ""
    diagnostic_interval = ""
    navigation = None
    task_context = None
    if payload:
        action_type = _as_text(payload.get("type")).lower()
        action_label = _payload_action_label(payload)
        set_node_target = _as_text(payload.get("set_node"))
        trace_id = _trace_id_from_payload(payload)
        tempo_url = tempo_trace_url(trace_id)
        navigation = _build_navigation_block(payload)
        task_context = _build_task_context(payload)
        diagnostic_kind = _as_text(payload.get("diagnostic"))
        diagnostic_attempts = _as_text(payload.get("attempts"))
        diagnostic_interval = _as_text(payload.get("interval"))
        ctx0 = payload.get("context")
        if isinstance(ctx0, dict):
            scenario_key = _as_text(ctx0.get("scenario"))
            if scenario_key:
                scenario_label = scenario_display_name(scenario_key)
            region_label = _as_text(payload.get("region")) or _as_text(ctx0.get("approval_region"))
        else:
            region_label = _as_text(payload.get("region"))
        if region_label:
            labeling_href = _labeling_href_for_region(client, instance_id, region_label)

    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id,
        payload=payload,
        source=image_source,
    )
    width, height = image_dimensions(png) if png else (0, 0)
    overlays: list[dict[str, Any]] = []
    if payload and png and width > 0 and height > 0:
        area_path = repo_root() / "area.json"
        overlays = [
            dict(o)
            for o in build_overlays(
                payload=payload,
                image_width=width,
                image_height=height,
                area_path=area_path,
                client=client,
                instance_id=instance_id,
            )
        ]

    x, y = None, None
    if payload:
        from api.services.click_approval_overlay import _tap_coords as tap_coords

        x, y = tap_coords(payload)

    return {
        "instance_id": instance_id,
        "approval_enabled": enabled,
        # Heartbeat is written by ``touch_heartbeat`` above whenever approval mode
        # is ON; expose it as a derived flag so the UI can render an explicit
        # "Heartbeat: ON / OFF" status line without a second round-trip.
        "heartbeat_active": enabled,
        "has_pending": payload is not None,
        "pending": payload,
        "scenario_key": scenario_key,
        "scenario_label": scenario_label,
        "region_label": region_label,
        "action_type": action_type,
        "action_label": action_label,
        "set_node_target": set_node_target,
        "trace_id": trace_id,
        "tempo_trace_url": tempo_url,
        "labeling_href": labeling_href,
        "diagnostic_kind": diagnostic_kind,
        "diagnostic_attempts": diagnostic_attempts,
        "diagnostic_interval": diagnostic_interval,
        "navigation": navigation,
        "task_context": task_context,
        "tap_x": x,
        "tap_y": y,
        "preview": {
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
        },
        "overlays": overlays,
        "instance_state": instance_state,
        "current_screen": _as_text(instance_state.get("current_screen")),
        "active_player": _as_text(instance_state.get("active_player")),
        "active_player_in_game_id": get_active_player_in_game_id(client, instance_id),
        "scenario_progress": build_scenario_progress(client, instance_id, instance_state),
    }


def submit_decision(client: Any, instance_id: str, decision: str) -> bool:
    decision = decision.strip().lower()
    if decision not in {"approve", "reject", "skip"}:
        msg = f"invalid decision: {decision}"
        raise ValueError(msg)
    curr_key = _current_key(instance_id)
    raw = client.get(curr_key)
    if not raw:
        return False
    try:
        payload = json.loads(_as_text(raw))
    except json.JSONDecodeError:
        client.delete(curr_key)
        return False
    if not isinstance(payload, dict):
        client.delete(curr_key)
        return False
    response_key = _as_text(payload.get("response_key"))
    if response_key:
        client.set(response_key, decision, ex=120)
    client.delete(curr_key)
    from ui.dashboard_events import publish_dashboard_event

    publish_dashboard_event(
        client,
        topic="approval",
        instance_id=instance_id,
        reason=f"decision:{decision}",
    )
    return True
