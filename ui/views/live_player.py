"""Operate: live Redis snapshot per player (buildings today; room for troops and more)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from config.buildings import get_building_registry
from config.devices import load_devices
from config.loader import load_settings
from ui.bot_services import ensure_embedded_bot
from ui.redis_client import get_instance_state, get_player_state_hash, require_redis_connection

ensure_embedded_bot()

_LEVEL_PREFIX = "buildings.levels."


def _levels_table_rows(state: dict[str, str]) -> list[dict[str, object]]:
    reg = get_building_registry()
    rows: list[dict[str, object]] = []
    for key, val in state.items():
        if not key.startswith(_LEVEL_PREFIX):
            continue
        bid = key[len(_LEVEL_PREFIX) :].strip()
        if not bid:
            continue
        try:
            lv = int(str(val).strip())
        except ValueError:
            lv = str(val).strip() or "—"
        bdef = reg.by_id(bid)
        display = bdef.name if bdef else bid
        cat = bdef.category if bdef else "—"
        rows.append(
            {
                "id": bid,
                "building": display,
                "category": cat,
                "level": lv,
            }
        )
    rows.sort(key=lambda r: str(r["building"]).lower())
    return rows


st.title("Live player")

st.caption(
    "Live data from Redis ``wos:player:<id>:state``. "
    "Building levels use ``buildings.levels.<id>`` (Century ``fetch_player``, furnace, bot sync). "
    "Additional sections (troops, resources, …) will live on this page as we wire them."
)

def _query_param_player_id() -> str:
    raw = st.query_params.get("player_id")
    if raw is None:
        return ""
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else ""
    return str(raw).strip()


settings = load_settings()
client = require_redis_connection()
devices = load_devices()
known_ids = sorted(devices.all_player_ids())

inst_options = [""] + [i.instance_id for i in settings.instances]
instance_pick = st.selectbox(
    "Instance (suggested active player)",
    options=inst_options,
    format_func=lambda x: "(none)" if x == "" else x,
)

hint_pid = ""
if instance_pick:
    hint_pid = (get_instance_state(client, instance_pick).get("active_player") or "").strip()

url_pid = _query_param_player_id()

if known_ids:
    idx_default = 0
    if url_pid and url_pid in known_ids:
        idx_default = known_ids.index(url_pid)
    elif hint_pid and hint_pid in known_ids:
        idx_default = known_ids.index(hint_pid)
    pick = st.selectbox("Player id", options=known_ids, index=idx_default)
else:
    pick = st.text_input(
        "Player id",
        value=url_pid or hint_pid,
        help="No gamers listed in db/devices.yaml — enter an id manually.",
    )

effective_pid = str(pick or "").strip()


@st.fragment(run_every=timedelta(seconds=3))
def _live_panel(pid: str) -> None:
    if not pid:
        st.info("Choose or enter a player id.")
        return

    state = get_player_state_hash(client, pid)
    if not state:
        st.warning(
            f"No Redis hash at ``wos:player:{pid}:state`` "
            "(worker has not written state yet, or key prefix differs)."
        )
        return

    nick = (state.get("nickname") or "").strip()
    stove = (state.get("stove_level") or "").strip()
    kid = (state.get("kid") or "").strip()
    avatar_url = (state.get("avatar_image") or "").strip()

    av_col, metrics_col = st.columns([1, 5], vertical_alignment="center")
    with av_col:
        if avatar_url:
            try:
                st.image(avatar_url, width=80)
            except Exception:
                st.caption("Avatar URL failed to load")
        else:
            st.caption("—")
    with metrics_col:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Nickname", nick or "—")
        c2.metric("Stove (Century)", stove or "—")
        c3.metric("KID", kid or "—")
        c4.metric("Player hash fields", len(state))

    st.subheader("Building levels")
    rows = _levels_table_rows(state)
    if not rows:
        st.info(
            "No ``buildings.levels.*`` keys yet — wait for ``fetch_player`` or building sync from the bot."
        )
    else:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


_live_panel(effective_pid)
