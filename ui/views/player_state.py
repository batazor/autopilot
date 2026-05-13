"""Player state: live Redis hash ``wos:player:<id>:state`` and persisted ``db/state.yaml``."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from century.api import CenturyAPIError, CenturyClient
from config.buildings import get_building_registry
from config.devices import load_devices, upsert_device_gamer
from config.heroes import HeroDef, get_hero_registry
from config.loader import load_settings
from config.state_schema import GamerState, StateDB
from config.state_store import get_state_store
from ui.bot_services import ensure_embedded_bot
from ui.redis_client import get_instance_state, get_player_state_hash, require_redis_connection

ensure_embedded_bot()

_LEVEL_PREFIX = "buildings.levels."


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_state(path: Path) -> tuple[StateDB | None, str | None, str]:
    if not path.is_file():
        return StateDB(), None, ""
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text) or {}
        return StateDB.model_validate(raw), None, text
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", text


def _gamer_summary_row(g: GamerState) -> dict[str, object]:
    b = g.buildings
    hud = b.state.text or ""
    bldg_hud = (hud[:40] + "…") if len(hud) > 40 else (hud or "—")
    return {
        "id": g.id,
        "nickname": g.nickname or "—",
        "power": g.power,
        "gems": g.gems,
        "furnace Lv": b.furnace.level,
        "furnace pwr": b.furnace.power,
        "bldg HUD": bldg_hud,
        "queue1": b.queue1 or "—",
        "queue2": b.queue2 or "—",
        "alliance": g.alliance.name or "—",
        "ally pwr": g.alliance.power,
        "expl Lv": g.exploration.level,
        "expl pwr": g.exploration.state.myPower,
        "arena rank": g.arena.rank,
        "arena pwr": g.arena.myPower,
        "wood": g.resources.wood,
        "food": g.resources.food,
        "contentment": g.chief.contentment,
    }


def _render_gamer_panels(g: GamerState) -> None:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Power", g.power)
    with c2:
        st.metric("Gems", g.gems)
    with c3:
        st.metric("Furnace Lv", g.buildings.furnace.level)
    with c4:
        st.metric("Furnace pwr", g.buildings.furnace.power)

    st.subheader("Buildings")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "queue1": g.buildings.queue1 or "—",
                    "queue2": g.buildings.queue2 or "—",
                    "hud": g.buildings.state.text or "—",
                }
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Resources")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "wood": g.resources.wood,
                    "food": g.resources.food,
                    "iron": g.resources.iron,
                    "meat": g.resources.meat,
                    "silver keys": g.resources.silver_keys,
                    "gold keys": g.resources.gold_keys,
                    "diamond": g.resources.diamond,
                }
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Events · recruitment")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "free recruitments today": g.events.recruitment.free_recruitments_today,
                }
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Troops")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "infantry": g.troops.infantry.state.TextStatus or "—",
                    "lancer": g.troops.lancer.state.TextStatus or "—",
                    "marksman": g.troops.marksman.state.TextStatus or "—",
                    "available infantry": g.troops.infantry.state.isAvailable,
                    "available lancer": g.troops.lancer.state.isAvailable,
                    "available marksman": g.troops.marksman.state.isAvailable,
                }
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Alliance / Exploration / Arena")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "alliance": g.alliance.name or "—",
                    "alliance power": g.alliance.power,
                    "members": f"{g.alliance.members.count}/{g.alliance.members.max}",
                    "exploration Lv": g.exploration.level,
                    "exploration power": g.exploration.state.myPower,
                    "arena rank": g.arena.rank,
                    "arena power": g.arena.myPower,
                    "contentment": g.chief.contentment,
                }
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def _infer_instance_id_for_player(player_id: str) -> str:
    try:
        reg = load_devices()
        dev = reg.get_device_for_player(player_id)
        if dev is not None and dev.name.strip():
            return dev.name.strip()
    except Exception:
        pass
    try:
        settings = load_settings()
        if settings.instances:
            return settings.instances[0].instance_id
    except Exception:
        pass
    return ""


def _sync_selected_player_from_century(g: GamerState) -> None:
    fid = int(g.id)
    now = time.time()
    try:
        data = asyncio.run(CenturyClient().fetch_player(fid))
    except CenturyAPIError as exc:
        st.warning(f"Century API error: {exc}")
        return
    except Exception as exc:
        st.error(f"Century sync failed: {type(exc).__name__}: {exc}")
        return

    try:
        store = get_state_store().get_or_create(str(fid), nickname=data.nickname)
        store.update_from_flat(
            {
                "nickname": data.nickname,
                "kid": data.kid,
                "avatar": data.avatar_image or "",
                "buildings.furnace.level": data.stove_level,
                "buildings.furnace.power": data.stove_lv_content,
                "buildings.levels.furnace": int(data.stove_level),
                "century_player_sync_at": float(now),
            }
        )
    except Exception as exc:
        st.error(f"state.yaml persist failed: {type(exc).__name__}: {exc}")
        return

    try:
        iid = _infer_instance_id_for_player(str(fid))
        if iid:
            repo = _repo_root()
            upsert_device_gamer(
                path=repo / "db" / "devices.yaml",
                device_name=iid,
                player_id=str(fid),
                nickname=data.nickname,
            )
    except Exception:
        pass

    st.success(
        f"Synced `{data.nickname}` · stove `{data.stove_level}` · KID `{data.kid}` · fid `{fid}`"
    )
    st.rerun()


def _resolve_hero_icon(hero_id: str) -> Path | None:
    base = _repo_root() / "db" / "assets" / "wiki" / "heroes" / hero_id
    if not base.is_dir():
        return None
    exts = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        return None
    files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
    return files[0]


def _format_seen_at(ts: object) -> str:
    try:
        seen = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if seen <= 0:
        return "—"
    delta = max(0, int(time.time() - seen))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _hero_entries_rows(
    entries: dict[str, object],
) -> list[dict[str, object]]:
    reg = get_hero_registry()
    rows: list[dict[str, object]] = []
    for hid, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        hdef: HeroDef | None = reg.by_id(hid)
        name = str(raw.get("name") or (hdef.name if hdef else hid) or hid)
        try:
            level = int(raw.get("level"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            level = 0
        rows.append(
            {
                "id": hid,
                "hero": name,
                "level": level,
                "rarity": (hdef.rarity if hdef else "") or "—",
                "class": (hdef.hero_class if hdef else "") or "—",
                "sub_class": (hdef.sub_class if hdef else "") or "—",
                "seen": _format_seen_at(raw.get("seen_at")),
            }
        )
    rows.sort(key=lambda r: (-(int(r["level"]) if isinstance(r["level"], int) else 0), str(r["hero"]).lower()))
    return rows


def _render_heroes_panel(g: GamerState) -> None:
    entries_raw = g.heroes.entries or {}
    entries: dict[str, object] = {
        str(k): v for k, v in entries_raw.items() if isinstance(v, dict)
    }
    reg = get_hero_registry()
    total_registry = len(reg.heroes)

    c1, c2, c3 = st.columns(3)
    c1.metric("Seen heroes", len(entries))
    c2.metric("Heroes in registry", total_registry)
    c3.metric("Notify", "yes" if g.heroes.isnotify else "no")

    if not entries:
        st.info(
            "No hero snapshots yet for this player. Open a hero card in-game "
            "and run the `sync_hero_unit` scenario — entries land in "
            "`heroes.entries.<id>` of `db/state.yaml`."
        )
    else:
        rows = _hero_entries_rows(entries)

        show_filter = st.text_input(
            "Filter (name / id / class)",
            value="",
            key=f"player_state_heroes_filter_{g.id}",
        ).strip().lower()

        visible: list[dict[str, object]] = []
        for r in rows:
            hay = " ".join(str(v) for v in r.values()).lower()
            if show_filter and show_filter not in hay:
                continue
            visible.append(r)

        if not visible:
            st.info("No heroes matched the current filter.")
        else:
            st.subheader(f"Owned heroes ({len(visible)}/{len(rows)})")
            cols_per_row = 4
            for i in range(0, len(visible), cols_per_row):
                chunk = visible[i : i + cols_per_row]
                tiles = st.columns(cols_per_row)
                for col, row in zip(tiles, chunk):
                    with col:
                        icon = _resolve_hero_icon(str(row["id"]))
                        if icon is not None and icon.is_file():
                            try:
                                st.image(str(icon), width=96)
                            except Exception:
                                pass
                        st.markdown(f"**{row['hero']}** · `{row['id']}`")
                        st.caption(
                            f"Lv. {row['level']} · {row['rarity']} · "
                            f"{row['class']} / {row['sub_class']}"
                        )
                        st.caption(f"seen: {row['seen']}")

            st.divider()
            st.markdown("**Table view**")
            st.dataframe(pd.DataFrame(visible), width="stretch", hide_index=True)

    with st.expander("Heroes not yet seen", expanded=False):
        seen_ids = set(entries.keys())
        missing = [h for h in reg.heroes if h.id not in seen_ids]
        if not missing:
            st.success("All heroes from the registry have been seen.")
        else:
            missing_rows = [
                {
                    "id": h.id,
                    "hero": h.name,
                    "rarity": h.rarity or "—",
                    "class": h.hero_class or "—",
                    "sub_class": h.sub_class or "—",
                }
                for h in missing
            ]
            missing_rows.sort(key=lambda r: str(r["hero"]).lower())
            st.dataframe(pd.DataFrame(missing_rows), width="stretch", hide_index=True)

    with st.expander("Raw `heroes.entries`", expanded=False):
        st.json(entries_raw)


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


def _query_param_player_id() -> str:
    raw = st.query_params.get("player_id")
    if raw is None:
        return ""
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else ""
    return str(raw).strip()


st.title("Player state")
st.caption(
    "Choose an account once. **Redis** — live ``wos:player:<id>:state`` from the worker. "
    "**Persisted** — ``db/state.yaml`` (``GamerState`` from game UI / bot)."
)

repo = _repo_root()
state_path = repo / "db" / "state.yaml"
rel = state_path.relative_to(repo).as_posix()

if state_path.is_file():
    db, yaml_err, raw_yaml_text = _load_state(state_path)
else:
    db, yaml_err, raw_yaml_text = StateDB(), None, ""

yaml_ids = {str(g.id) for g in db.gamers} if db else set()

settings = load_settings()
client = require_redis_connection()
devices = load_devices()
known_ids = sorted(set(devices.all_player_ids()) | yaml_ids)

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
        help="No gamers in devices/state — enter an id manually.",
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


tab_redis, tab_yaml, tab_heroes = st.tabs(
    ["Redis (live)", "Persisted (state.yaml)", "Heroes"]
)

with tab_redis:
    _live_panel(effective_pid)

with tab_heroes:
    st.markdown(f"**File:** `{rel}` · section `gamers[].heroes`")
    if yaml_err:
        st.error(f"Cannot parse YAML as `StateDB`: {yaml_err}")
    elif not state_path.is_file():
        st.warning(f"Missing `{rel}` — no player heroes to display yet.")
    elif not db or not db.gamers:
        st.info("No gamers in `gamers` yet — run scenarios that persist state.")
    else:
        ids = [str(g.id) for g in db.gamers]
        idx_sel = ids.index(effective_pid) if effective_pid in ids else 0

        def _hero_tab_label(g: GamerState) -> str:
            nick = (g.nickname or "").strip() or "—"
            return f"{nick} · {g.id}"

        if len(db.gamers) > 1:
            selected_hero_gamer = st.selectbox(
                "Player",
                options=list(db.gamers),
                index=idx_sel,
                format_func=_hero_tab_label,
                key="player_state_selected_heroes",
            )
        else:
            selected_hero_gamer = db.gamers[0]

        st.subheader(
            f"{(selected_hero_gamer.nickname or '—').strip() or '—'} · "
            f"`{selected_hero_gamer.id}`"
        )
        _render_heroes_panel(selected_hero_gamer)

with tab_yaml:
    st.markdown(f"**File:** `{rel}`")

    if st.button("Reload from disk", key="player_state_reload_yaml"):
        st.rerun()

    if yaml_err:
        st.error(f"Cannot parse YAML as `StateDB`: {yaml_err}")
        with st.expander("Raw file (edit until it validates)", expanded=True):
            st.code(raw_yaml_text or "(empty)", language="yaml")
    elif not state_path.is_file():
        st.warning(f"Missing `{rel}` — it will be created when the first gamer state is saved.")
    elif not db or not db.gamers:
        st.info("No gamers in `gamers` yet — run scenarios that persist state.")
    else:
        q = st.text_input(
            "Filter (id / nickname / alliance / building text)",
            value="",
            key="player_state_filter",
        ).strip().lower()

        filtered: list[GamerState] = []
        for g in db.gamers:
            row = _gamer_summary_row(g)
            hay = " ".join(str(v) for v in row.values()).lower()
            if q and q not in hay:
                continue
            filtered.append(g)

        if not filtered:
            st.info("No gamers matched the current filter.")
        else:

            def _label(g: GamerState) -> str:
                nick = (g.nickname or "").strip() or "—"
                ally = (g.alliance.name or "").strip()
                suffix = f" · {ally}" if ally else ""
                return f"{nick} · {g.id}{suffix}"

            if len(filtered) > 1:
                ids = [str(g.id) for g in filtered]
                idx_sel = ids.index(effective_pid) if effective_pid in ids else 0
                selected = st.selectbox(
                    "Player",
                    options=filtered,
                    index=idx_sel,
                    format_func=_label,
                    key="player_state_selected_yaml",
                )
            else:
                selected = filtered[0]

            h1, h2 = st.columns([3, 1], vertical_alignment="center")
            with h1:
                st.subheader(f"{(selected.nickname or '—').strip() or '—'} · `{selected.id}`")
            with h2:
                if st.button("Sync from Century API", key=f"player_state_sync_{selected.id}"):
                    _sync_selected_player_from_century(selected)
            _render_gamer_panels(selected)

        payload = db.model_dump(mode="json")
        st.download_button(
            "Download full state (JSON)",
            data=json.dumps(payload, indent=2, ensure_ascii=False),
            file_name="state_export.json",
            mime="application/json",
            key="player_state_download_json",
        )

        st.divider()
        st.markdown("**Full record per gamer**")
        for g in db.gamers:
            label = f"`{g.id}` · {g.nickname or '(no nickname)'}"
            with st.expander(label, expanded=False):
                st.json(g.model_dump(mode="json"))
