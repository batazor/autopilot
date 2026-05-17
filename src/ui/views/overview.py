"""Home dashboard: fleet health, quick navigation, devices, player identity."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse, urlunparse

import redis as _redis_pkg
import streamlit as st
from streamlit_nested_table import nested_table, table_column

from config.devices import DeviceRegistry, load_devices
from config.loader import InstanceConfig, load_settings
from config.module_registry import list_wiki_modules
from config.module_ui_registry import iter_module_ui_page_specs
from ui.bot_services import ensure_embedded_bot, restart_embedded_bot
from ui.keys import OVERVIEW_FEEDBACK
from ui.redis_client import (
    count_claimed_slots,
    count_queue_tasks,
    get_instance_state,
    push_instance_command,
    require_redis_connection,
)

ensure_embedded_bot()

_REPO = Path(__file__).resolve().parents[2]
_LOGO = _REPO / "docs" / "logo.png"


@dataclass(frozen=True)
class _QuickLink:
    page: str
    label: str
    help: str


@dataclass(frozen=True)
class _QuickSection:
    title: str
    caption: str
    links: tuple[_QuickLink, ...]


_QUICK_SECTIONS: tuple[_QuickSection, ...] = (
    _QuickSection(
        "Operate",
        "Live workers, queue, and accounts",
        (
            _QuickLink("views/instance.py", "Instance", "Screenshots, pause/resume, manual tasks."),
            _QuickLink("views/player_state.py", "Player state", "Redis hash + db/state.yaml per gamer."),
            _QuickLink("views/queue.py", "Queue", "Pending and running tasks per instance."),
        ),
    ),
    _QuickSection(
        "Wiki & labeling",
        "References, area.json, scenarios",
        (
            _QuickLink("views/labeling.py", "Labeling", "Draw regions on reference screenshots."),
            _QuickLink("views/gallery.py", "Gallery", "Browse reference PNGs by module."),
            _QuickLink("views/edit_scenarios.py", "Scenarios editor", "Structured DSL scenario forms."),
            _QuickLink("views/wiki_analyze.py", "Analyze", "Overlay rules vs area.json audit."),
        ),
    ),
    _QuickSection(
        "Debug",
        "Runner, approvals, routes",
        (
            _QuickLink("views/debug_scenarios.py", "Scenario runner", "Force-run one scenario on an instance."),
            _QuickLink("views/click_approvals.py", "Click approvals", "Pending human-approved taps."),
            _QuickLink("views/routes.py", "Routes", "Node graph and screen transitions."),
        ),
    ),
    _QuickSection(
        "Config",
        "Scenarios, devices, economy",
        (
            _QuickLink("views/scenarios.py", "Scenarios", "Enable/disable YAML scenarios + cron."),
            _QuickLink("views/adb_devices.py", "ADB", "devices.yaml and instance wiring."),
            _QuickLink("views/balance.py", "Balance", "Resource / economy snapshots."),
        ),
    ),
)


def _module_quick_links(repo_root: Path) -> tuple[_QuickLink, ...]:
    """Module UI pages (absolute paths for ``st.page_link``)."""

    out: list[_QuickLink] = []
    for spec in iter_module_ui_page_specs(repo_root):
        out.append(
            _QuickLink(
                str(spec.path),
                spec.title,
                f"Module `{spec.module_id}` — {spec.nav_group}",
            )
        )
    return tuple(out)


def _quick_sections_for_repo(repo_root: Path) -> tuple[_QuickSection, ...]:
    module_links = _module_quick_links(repo_root)
    if not module_links:
        return _QUICK_SECTIONS
    sections: list[_QuickSection] = []
    for section in _QUICK_SECTIONS:
        if section.title != "Config":
            sections.append(section)
            continue
        sections.append(
            _QuickSection(
                section.title,
                section.caption,
                section.links + module_links,
            )
        )
    return tuple(sections)

_DEVICES_HELP = (
    "Rows mirror Redis `wos:instance:<id>:state`. **live** = heartbeat <10s. "
    "Expand a row to see gamer accounts for that instance (from **db/devices.yaml**). "
    "Use **Fleet actions** below to pause/resume or open Instance."
)


def _overview_table_height(n: int, cap: int) -> int:
    return min(48 + max(n, 1) * 34, cap)


def _internal_page_url(page: str, query: dict[str, str] | None = None) -> str:
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts:
        parts[-1] = page
        path = "/" + "/".join(parts)
    else:
        path = "/" + page
    q = urlencode(query or {})
    return urlunparse((u.scheme, u.netloc, path, "", q, ""))


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


def _format_age(unix_ts: object) -> str:
    try:
        ts = float(unix_ts)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
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


def _read_player_state_decoded(redis_client: _redis_pkg.Redis, pid: str) -> dict[str, str]:
    try:
        # redis-py stubs union sync + async returns. Narrow back to the
        # concrete sync ``dict[str, str]`` — every client in this module
        # is built with ``decode_responses=True``.
        raw = cast("dict[str, str]", redis_client.hgetall(f"wos:player:{pid}:state")) or {}
    except Exception:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else str(v)
        )
        for k, v in raw.items()
    }


def _fleet_status(row: dict[str, str]) -> str:
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


def _fleet_task_label(row: dict[str, str]) -> str:
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


def _fleet_alert(row: dict[str, str]) -> str:
    err = (row.get("last_error") or "").strip()
    blocked = (row.get("queue_blocked_reason") or "").strip()
    parts = [p for p in (err, blocked) if p]
    return " · ".join(parts) if parts else ""


def _fleet_players_nested_columns() -> list[dict[str, Any]]:
    """Single schema for fleet parent rows + per-gamer sub-rows (same ``nested_table`` columns)."""
    return [
        table_column("who", "Instance / account", width=118),
        table_column(
            "open",
            "→",
            width=88,
            cell_type="link",
            link_text_key="open_label",
        ),
        table_column(
            "status",
            "Status",
            width=112,
            cell_type="pill",
            pill_preset="fleet_status",
        ),
        table_column("active_player", "Active", width=110),
        table_column("on_device", "On inst.", width=78, align="center"),
        table_column("node", "Node", width=130),
        table_column("task", "Task", width=260),
        table_column("uptime", "Uptime", width=88),
        table_column("alert", "Alert", width=200),
        table_column("nickname", "Nickname", width=130),
        table_column("in_game_id", "In-game ID", width=118),
        table_column("ocr_conf", "ID conf", width=80),
        table_column("ocr_age", "ID age", width=88),
        table_column("stove", "Stove", width=72),
        table_column("kid", "KID", width=72),
        table_column("century", "Century", width=88),
    ]


def _player_row_fleet_nested(
    client: _redis_pkg.Redis,
    instance_id: str,
    player_id: str,
    *,
    active_players: set[str],
) -> dict[str, Any]:
    """One gamer row using the same column schema as the fleet parent row."""
    state = _read_player_state_decoded(client, player_id)
    ig_id = (state.get("player_id") or "").strip()
    conf_s = (state.get("player_id_confidence") or "").strip()
    try:
        conf_disp = f"{float(conf_s):.2f}" if conf_s else ""
    except ValueError:
        conf_disp = conf_s
    return {
        "id": f"{instance_id}:{player_id}",
        "who": player_id,
        "open": _internal_page_url("player_state", {"player_id": player_id}),
        "open_label": "State",
        "status": "",
        "active_player": "",
        "on_device": "●" if player_id in active_players else "",
        "node": "",
        "task": "",
        "uptime": "",
        "alert": "",
        "nickname": (state.get("nickname") or "").strip() or "—",
        "in_game_id": ig_id or "—",
        "ocr_conf": conf_disp or "—",
        "ocr_age": _format_age(state.get("player_id_at") or 0.0),
        "stove": (state.get("stove_level") or "").strip() or "—",
        "kid": (state.get("kid") or "").strip() or "—",
        "century": _format_age(state.get("century_player_sync_at") or 0.0),
    }


def _build_fleet_players_rows(
    client: _redis_pkg.Redis,
    instances: list[InstanceConfig],
    db_registry: DeviceRegistry,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Fleet table rows with ``subRows`` = gamers for that instance (devices.yaml)."""
    by_name = {str(d.name): d for d in db_registry.devices}
    active_players: set[str] = set()
    inst_ids: list[str] = []
    states: list[dict[str, str]] = []
    for inst in instances:
        iid = str(getattr(inst, "instance_id", ""))
        inst_ids.append(iid)
        row = get_instance_state(client, iid)  # type: ignore[arg-type]
        states.append(row)
        ap = (row.get("active_player") or "").strip()
        if ap and ap != "—":
            active_players.add(ap)

    rows: list[dict[str, Any]] = []
    for iid, row in zip(inst_ids, states, strict=True):
        sub_rows: list[dict[str, Any]] = []
        dev = by_name.get(iid)
        if dev is not None:
            for gamer in dev.all_gamers():
                pid = str(gamer.id)
                sub_rows.append(
                    _player_row_fleet_nested(client, iid, pid, active_players=active_players)
                )
        rows.append(
            {
                "id": iid,
                "instance": iid,
                "who": iid,
                "open": _internal_page_url("instance", {"instance_id": iid}),
                "open_label": "Open",
                "status": _fleet_status(row),
                "active_player": (row.get("active_player") or "").strip() or "—",
                "on_device": "",
                "node": (row.get("current_screen") or "").strip() or "—",
                "task": _fleet_task_label(row),
                "uptime": _elapsed_since(row.get("worker_started_at") or "") or "—",
                "alert": _fleet_alert(row),
                "nickname": "",
                "in_game_id": "",
                "ocr_conf": "",
                "ocr_age": "",
                "stove": "",
                "kid": "",
                "century": "",
                "_paused": row.get("paused") == "1",
                "subRows": sub_rows,
            }
        )
    return rows, active_players


def _render_fleet_actions(
    client: _redis_pkg.Redis,
    fleet_rows: list[dict[str, Any]],
) -> None:
    if not fleet_rows:
        return
    instances = [str(r["instance"]) for r in fleet_rows]
    paused_map = {str(r["instance"]): bool(r.get("_paused")) for r in fleet_rows}
    st.caption("Fleet actions")
    a1, a2, a3 = st.columns([2.2, 1, 1.2], vertical_alignment="bottom")
    with a1:
        pick = st.selectbox(
            "Instance",
            options=instances,
            key="ov_fleet_action_pick",
            label_visibility="collapsed",
        )
    with a2:
        is_paused = paused_map.get(pick, False)
        label = "Resume" if is_paused else "Pause"
        if st.button(label, key="ov_fleet_action_toggle", width="stretch"):
            cmd = "resume" if is_paused else "pause"
            push_instance_command(client, pick, {"cmd": cmd})
            st.session_state[OVERVIEW_FEEDBACK] = f"`{pick}`: {cmd} sent."
            st.rerun()
    with a3:
        st.page_link(
            "views/instance.py",
            label="Open instance",
            query_params={"instance_id": pick},
            width="stretch",
        )


def _render_quick_nav() -> None:
    st.subheader("Quick navigation")
    sections = _quick_sections_for_repo(_REPO)
    cols = st.columns(len(sections), gap="medium")
    for col, section in zip(cols, sections, strict=True):
        with col, st.container(border=True):
            st.markdown(f"**{section.title}**")
            st.caption(section.caption)
            for link in section.links:
                st.page_link(
                    link.page,
                    label=link.label,
                    help=link.help,
                    width="stretch",
                )


def _render_hero() -> None:
    left, right = st.columns([4, 1.2], vertical_alignment="center")
    with left:
        if _LOGO.exists():
            st.image(str(_LOGO), width=220)
        st.markdown(
            "### Whiteout Survival Autopilot\n"
            "Multi-account bot — workers, queue, DSL scenarios, and wiki tooling in one UI."
        )
        modules = [m for m in list_wiki_modules(_REPO) if m.module_id]
        if modules:
            names = ", ".join(f"`{m.module_id}`" for m in modules[:8])
            extra = len(modules) - 8
            suffix = f" · +{extra} more" if extra > 0 else ""
            st.caption(f"Feature modules: {names}{suffix}")
    with right:
        if st.button(
            "Restart bot",
            type="primary",
            width="stretch",
            help="Restart embedded workers and scheduler in this process",
        ):
            try:
                restart_embedded_bot()
            except RuntimeError as exc:
                st.error(str(exc))
                st.stop()
            st.success("Bot restarted")
            st.rerun()
        st.page_link(
            "views/player_state.py",
            label="All players",
            width="stretch",
            help="Redis + persisted state for every registered gamer",
        )


def _count_live_instances(
    client: _redis_pkg.Redis,
    instances: list[InstanceConfig],
) -> tuple[int, int, int]:
    live = paused = busy = 0
    for inst in instances:
        iid = getattr(inst, "instance_id", "")
        row = get_instance_state(client, iid)  # type: ignore[arg-type]
        if row.get("paused") == "1":
            paused += 1
        if _fleet_status(row) == "live":
            live += 1
        if (row.get("state") or "").strip().lower() == "busy":
            busy += 1
    return live, paused, busy


@st.fragment(run_every=timedelta(seconds=2))
def _live_dashboard() -> None:
    st.session_state.setdefault(OVERVIEW_FEEDBACK, None)
    fb = st.session_state.get(OVERVIEW_FEEDBACK)
    if fb:
        c1, c2 = st.columns([11, 1])
        with c1:
            st.info(fb)
        with c2:
            if st.button("✕", key="ov_dismiss", help="Dismiss"):
                st.session_state[OVERVIEW_FEEDBACK] = None
                st.rerun()

    settings = load_settings()
    try:
        client = require_redis_connection()
    except Exception as exc:
        st.error(f"Redis unavailable: {exc}")
        return

    db_registry = load_devices()
    instances = settings.instances
    n_inst = len(instances)
    q = count_queue_tasks(client)
    claimed = count_claimed_slots(client)
    live, paused, busy = _count_live_instances(client, instances)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Instances", n_inst, help="Configured in settings.yaml / devices.yaml")
    m2.metric("Live workers", live, delta=None if live == n_inst else f"{live}/{n_inst}")
    m3.metric("Queue", q, help="Pending tasks across all instance queues")
    m4.metric("Busy", busy, help="Workers with state=busy or active task timestamp")
    m5.metric("Locks", claimed, help="Cooperative claim keys in Redis")

    if paused:
        st.caption(f"**{paused}** instance(s) paused — resume from the table below.")

    st.divider()

    st.subheader("Fleet", help=_DEVICES_HELP)
    if not instances:
        st.warning("No instances in **config/settings.yaml**. Wire ADB serials first.")
        st.page_link("views/adb_devices.py", label="Open ADB setup", icon="📱")
        return

    if not db_registry.devices:
        st.info("No entries in **db/devices.yaml** — add devices and gamer IDs in ADB.")
        st.page_link("views/adb_devices.py", label="Configure devices", icon="📱")
        return

    st.caption(
        "Gamer rows (**expand** the instance): identity from **`who_i_am`** (OCR) and **`fetch_player`** (Century). "
        "Source: **db/devices.yaml**."
    )
    fleet_df, _ = _build_fleet_players_rows(client, instances, db_registry)
    n_sub = sum(len(r.get("subRows") or []) for r in fleet_df)
    nested_table(
        fleet_df,
        _fleet_players_nested_columns(),
        height=_overview_table_height(len(fleet_df) + min(n_sub, 24), 560),
        striped=True,
        compact=True,
        hide_expand=False,
        key="ov_fleet_nested",
    )
    _render_fleet_actions(client, fleet_df)


_render_hero()
_render_quick_nav()
_live_dashboard()
