"""Dashboard overview: instances table, queue summary, quick actions."""

from __future__ import annotations

import time
from datetime import timedelta

import streamlit as st

from config.devices import load_devices
from config.loader import load_settings
from ui.bot_services import ensure_embedded_bot, restart_embedded_bot
from ui.keys import OVERVIEW_FEEDBACK
from ui.redis_client import (
    count_claimed_slots,
    count_queue_tasks,
    get_instance_state,
    get_player_fsm,
    push_instance_command,
    require_redis_connection,
)

ensure_embedded_bot()

st.title("Overview")

st.page_link(
    "views/player_state.py",
    label="Player state",
    help="Redis live hash and persisted db/state.yaml per account.",
)

if st.button("Restart bot", help="Stop and start embedded workers/scheduler"):
    restart_embedded_bot()
    st.success("Bot restart triggered")
    st.rerun()


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
        delta = time.time() - float(ts_str)
    except ValueError:
        return None
    return _format_elapsed(delta)


def _is_recent(ts_str: str, *, max_age_s: float) -> bool:
    ts_str = ts_str.strip()
    if not ts_str:
        return False
    try:
        ts = float(ts_str)
    except ValueError:
        return False
    return (time.time() - ts) <= max_age_s


def _format_age(unix_ts: object) -> str:
    """Compact, human-friendly delta for ``*_at`` Redis fields. ``"—"`` on garbage input."""
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


def _read_player_state_decoded(redis_client: object, pid: str) -> dict[str, str]:
    """Decoded ``wos:player:<pid>:state`` hash; ``{}`` on Redis errors."""
    try:
        raw = redis_client.hgetall(f"wos:player:{pid}:state") or {}  # type: ignore[attr-defined]
    except Exception:
        return {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else str(v)
        )
        for k, v in raw.items()
    }


def _render_player_identity(
    redis_client: object,
    pid: str,
    *,
    fsm_state: str,
    is_active: bool,
) -> None:
    """Per-player identity block: avatar + OCR ``player_id`` + Century profile.

    Sources:

    - ``ocr: player_id`` (from ``who_i_am`` scenario) → ``player_id``,
      ``player_id_confidence``, ``player_id_at``.
    - ``exec: fetch_player`` (Century API) → ``nickname``, ``stove_level``,
      ``kid``, ``stove_lv_content``, ``avatar_image``, ``century_player_sync_at``.
    """
    state = _read_player_state_decoded(redis_client, pid)

    ig_id = (state.get("player_id") or "").strip()
    conf_s = (state.get("player_id_confidence") or "").strip()
    ocr_age = _format_age(state.get("player_id_at") or 0.0)

    nickname = (state.get("nickname") or "").strip()
    stove_level = (state.get("stove_level") or "").strip()
    kid = (state.get("kid") or "").strip()
    stove_content = (state.get("stove_lv_content") or "").strip()
    avatar = (state.get("avatar_image") or "").strip()
    century_age = _format_age(state.get("century_player_sync_at") or 0.0)

    badge = " · _active_" if is_active else ""
    header = f"**`{pid}`**{badge} · FSM `{fsm_state or 'unknown'}`"

    col_av, col_txt, col_live = st.columns([1, 5, 0.85], vertical_alignment="center")
    with col_live:
        st.page_link(
            "views/player_state.py",
            label="Player",
            query_params={"player_id": pid},
            help="Player state — Redis and db/state.yaml for this account.",
            width="stretch",
        )
    with col_av:
        if avatar:
            try:
                st.image(avatar, width=56)
            except Exception:
                st.caption("🛡️")
        else:
            st.caption("—")
    with col_txt:
        lines: list[str] = [header]
        if ig_id:
            try:
                conf_disp = f"{float(conf_s):.2f}" if conf_s else "—"
            except ValueError:
                conf_disp = conf_s or "—"
            lines.append(
                f"`player_id` → `{ig_id}` · conf `{conf_disp}` · {ocr_age}"
            )
        else:
            lines.append(
                "`player_id` → `—` _(not yet identified — runs `who_i_am`)_"
            )
        if nickname:
            parts = [f"**{nickname}**"]
            if stove_level:
                parts.append(f"stove `{stove_level}`")
            if kid:
                parts.append(f"KID `{kid}`")
            if stove_content:
                parts.append(f"stove_lv_content `{stove_content}`")
            parts.append(f"synced {century_age}")
            lines.append(" · ".join(parts))
        else:
            lines.append("_Century profile_ → `—` _(runs `exec: fetch_player`)_")
        st.markdown("  \n".join(lines))


def _device_status_cell(row: dict[str, str]) -> str:
    """Compact status cell with an explicit reason."""
    if not row:
        return "🔴 no redis state"
    if row.get("paused") == "1":
        return "🔴 paused"
    st_val = (row.get("state") or "").strip().lower()
    # Heartbeat-based liveness: state can be stale (e.g. restarting stuck) even while worker is alive.
    alive = _is_recent(row.get("last_seen_at") or "", max_age_s=10.0)
    if st_val in {"restarting", "crashed"}:
        return f"{'🟡' if alive else '🔴'} {st_val}{' (alive)' if alive else ''}"
    if not (row.get("worker_started_at") or "").strip():
        # Worker state exists but didn't record a start timestamp yet.
        return "🟡 starting"
    if alive:
        return "🟢 running"
    return "🔴 stale"


def _task_cell(row: dict[str, str]) -> str:
    """No task type in Redis — only whether a task is in progress and for how long."""
    st_val = (row.get("state") or "").strip().lower()
    started = (row.get("current_task_started_at") or "").strip()
    if st_val != "busy" and not started:
        return "—"
    elapsed = _elapsed_since(started) if started else None
    if elapsed:
        return f"busy · {elapsed}"
    return "busy"


_DEVICES_HELP = (
    "Device: instance id from settings. Status: shows worker health from Redis "
    "(e.g. no redis state / paused / crashed / restarting). "
    "Player: active account after a successful switch. "
    "Task: busy while a queue item runs (elapsed since task start). "
    "Session: uptime since the worker connected to Redis "
    "(resets when the worker restarts). "
    "Row: pause **or** resume (one control by worker state), open Instance page."
)

# Column weights: data + compact icon actions
_TABLE_COLS = [2.0, 0.8, 1.25, 1.25, 2.15, 1.25, 0.7, 0.7]


@st.fragment(run_every=timedelta(seconds=2))
def _dashboard() -> None:
    """Auto-refreshing section — only this fragment reruns every 2 s, not the full page."""
    st.session_state.setdefault(OVERVIEW_FEEDBACK, None)

    fb = st.session_state.overview_feedback
    if fb:
        msg_col, btn_col = st.columns([10, 1])
        with msg_col:
            st.info(fb)
        with btn_col:
            if st.button("✕", help="Dismiss", key="ov_dismiss_btn"):
                st.session_state.overview_feedback = None
                st.rerun()

    settings = load_settings()
    client = require_redis_connection()
    db_registry = load_devices()

    n_inst = len(settings.instances)
    n_players = len(db_registry.all_player_ids())
    q = count_queue_tasks(client)
    claimed = count_claimed_slots(client)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Instances", n_inst)
    c2.metric("Players", n_players)
    c3.metric("Queue tasks", q)
    c4.metric("Cooperative locks", claimed)

    st.divider()

    st.subheader("Devices", help=_DEVICES_HELP)
    st.caption(
        "**Worker is running** when Status is 🟢 and **Session** shows uptime "
        "(Redis `wos:instance:<id>:state`). Embedded bot starts from **`ui/app.py`** "
        "or when this Overview/Instance page loads standalone. "
        "**⏸ Pause** stops dequeuing tasks and **ADB rolling preview PNG** until **▶ Resume**."
    )

    if settings.instances:
        hdr = st.columns(_TABLE_COLS, vertical_alignment="center")
        hdr[0].markdown("**Device**")
        hdr[1].markdown("**Status**")
        hdr[2].markdown("**Player**")
        hdr[3].markdown("**Node**")
        hdr[4].markdown("**Task**")
        hdr[5].markdown("**Session**")
        hdr[6].markdown("**▶⏸**")
        hdr[7].markdown("**🔗**")

        for inst in settings.instances:
            row = get_instance_state(client, inst.instance_id)
            active = (row.get("active_player") or "").strip() or "—"
            session_uptime = _elapsed_since(row.get("worker_started_at") or "") or "—"
            status_cell = _device_status_cell(row)
            node = (row.get("current_screen") or "").strip() or "—"
            task_c = _task_cell(row)
            paused = row.get("paused") == "1"

            r = st.columns(_TABLE_COLS, vertical_alignment="center")
            r[0].code(inst.instance_id, language=None)
            r[1].write(status_cell)
            r[2].write(active)
            r[3].write(node)
            r[4].write(task_c)
            r[5].write(session_uptime)
            with r[6]:
                if paused:
                    if st.button(
                        "▶",
                        key=f"ov-resume-{inst.instance_id}",
                        help="Resume this instance worker (starts dequeuing tasks again).",
                        width="stretch",
                    ):
                        push_instance_command(client, inst.instance_id, {"cmd": "resume"})
                        st.session_state.overview_feedback = f"`{inst.instance_id}`: resume sent to worker."
                        st.rerun()
                else:
                    if st.button(
                        "⏸",
                        key=f"ov-pause-{inst.instance_id}",
                        help="Pause this instance worker (stops dequeuing tasks until resumed).",
                        width="stretch",
                    ):
                        push_instance_command(client, inst.instance_id, {"cmd": "pause"})
                        st.session_state.overview_feedback = f"`{inst.instance_id}`: pause sent to worker."
                        st.rerun()
            with r[7]:
                st.page_link(
                    "views/instance.py",
                    label="🔗",
                    query_params={"instance_id": inst.instance_id},
                    help=(
                        "Instance — screenshots, queue commands, FSM history "
                        f"for `{inst.instance_id}`."
                    ),
                    width="stretch",
                )

        # Collect all active players across instances for the "active" badge
        active_players: set[str] = set()
        for inst in settings.instances:
            inst_state = get_instance_state(client, inst.instance_id) or {}
            ap = (inst_state.get("active_player") or "").strip()
            if ap:
                active_players.add(ap)

        st.divider()

        if db_registry.devices:
            st.caption(
                "Player identity comes from the `who_i_am` scenario "
                "(`ocr: player_id`) and Century profile sync (`exec: fetch_player`). "
                "Source: **db/devices.yaml**."
            )
            for device in db_registry.devices:
                gamers = device.all_gamers()
                label = f"Players · {device.name} ({len(gamers)} account{'s' if len(gamers) != 1 else ''})"
                with st.expander(label, expanded=True):
                    if not gamers:
                        st.info("No players in this device entry.")
                    else:
                        for idx, gamer in enumerate(gamers):
                            if idx > 0:
                                st.divider()
                            pid = str(gamer.id)
                            fsm_state = get_player_fsm(client, pid) or "unknown"
                            _render_player_identity(
                                client,
                                pid,
                                fsm_state=fsm_state,
                                is_active=pid in active_players,
                            )
                st.divider()
        else:
            st.info("No players in **db/devices.yaml** — add devices and gamers there.")
    else:
        st.info("No instances in **config/settings.yaml**.")


_dashboard()
