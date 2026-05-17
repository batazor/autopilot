"""Player state: live Redis hash ``wos:player:<id>:state`` and persisted ``db/state.yaml``."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
import streamlit_nested_table as snt
import yaml

from century.api import CenturyAPIError, CenturyClient
from config.buildings import get_building_registry
from config.devices import load_devices, upsert_device_gamer
from config.heroes import HeroDef, get_hero_registry
from config.loader import load_settings
from config.paths import repo_root as default_repo_root
from config.state_schema import GamerState, StateDB
from config.state_store import get_state_store
from ui.bot_services import ensure_embedded_bot
from ui.redis_client import get_instance_state, get_player_state_hash, require_redis_connection

ensure_embedded_bot()

_LEVEL_PREFIX = "buildings.levels."

_RESOURCES_COL_ORDER: tuple[str, ...] = (
    "wood",
    "food",
    "iron",
    "meat",
    "silver keys",
    "gold keys",
    "diamond",
)

_OWNED_HERO_COL_ORDER: tuple[str, ...] = (
    "id",
    "hero",
    "level",
    "rarity",
    "class",
    "sub_class",
    "red_dot",
    "upgrade",
    "seen",
)

_LOCKED_HERO_COL_ORDER: tuple[str, ...] = (
    "id",
    "hero",
    "shards_current",
    "shards_required",
    "rarity",
    "class",
    "sub_class",
    "red_dot",
    "upgrade",
)

_MISSING_HERO_COL_ORDER: tuple[str, ...] = (
    "id",
    "hero",
    "rarity",
    "class",
    "sub_class",
)


def _player_state_table_height(n: int, cap: int) -> int:
    """Match Overview / Queue table body height heuristic."""
    return min(48 + max(n, 1) * 34, cap)


def _load_owned_hero_nested_rows(
    owned_visible: list[dict[str, object]],
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in owned_visible:
        row = {k: r.get(k) for k in _OWNED_HERO_COL_ORDER}
        row["id"] = str(r.get("id") or "")
        out.append(row)
    return out


def _load_locked_hero_nested_rows(
    locked_visible: list[dict[str, object]],
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in locked_visible:
        row = {k: r.get(k) for k in _LOCKED_HERO_COL_ORDER}
        row["id"] = str(r.get("id") or "")
        out.append(row)
    return out


def _owned_heroes_nested_columns() -> list[snt.TableColumn]:
    return [
        snt.table_column("id", "id", width=104),
        snt.table_column("hero", "hero", width=152),
        snt.table_column("level", "level", width=72, align="center"),
        snt.table_column("rarity", "rarity", width=88),
        snt.table_column("class", "class", width=88),
        snt.table_column("sub_class", "sub_class", width=112),
        snt.table_column("red_dot", "red_dot", width=80, cell_type="bool"),
        snt.table_column("upgrade", "upgrade", width=88, cell_type="bool"),
        snt.table_column("seen", "seen", width=96),
    ]


def _locked_heroes_nested_columns() -> list[snt.TableColumn]:
    return [
        snt.table_column("id", "id", width=104),
        snt.table_column("hero", "hero", width=152),
        snt.table_column("shards_current", "shards_current", width=112, align="right"),
        snt.table_column("shards_required", "shards_required", width=120, align="right"),
        snt.table_column("rarity", "rarity", width=88),
        snt.table_column("class", "class", width=88),
        snt.table_column("sub_class", "sub_class", width=112),
        snt.table_column("red_dot", "red_dot", width=80, cell_type="bool"),
        snt.table_column("upgrade", "upgrade", width=88, cell_type="bool"),
    ]


def _missing_heroes_nested_columns() -> list[snt.TableColumn]:
    return [
        snt.table_column("id", "id", width=104),
        snt.table_column("hero", "hero", width=168),
        snt.table_column("rarity", "rarity", width=88),
        snt.table_column("class", "class", width=88),
        snt.table_column("sub_class", "sub_class", width=112),
    ]


def _building_levels_nested_columns() -> list[snt.TableColumn]:
    return [
        snt.table_column("id", "ID", width=104),
        snt.table_column("building", "Building", width=168),
        snt.table_column("category", "Category", width=112),
        snt.table_column("level", "Level", width=80, align="center"),
        snt.table_column(
            "wiki",
            "→",
            width=72,
            cell_type="link",
            link_text_key="wiki_label",
        ),
    ]


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
    snt.nested_table(
        [
            {
                "id": f"ps_bldg_hud_{g.id}",
                "queue1": g.buildings.queue1 or "—",
                "queue2": g.buildings.queue2 or "—",
                "hud": g.buildings.state.text or "—",
            }
        ],
        [
            snt.table_column("queue1", "queue1", width=168),
            snt.table_column("queue2", "queue2", width=168),
            snt.table_column("hud", "hud", width=560),
        ],
        height=_player_state_table_height(1, 160),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_bldg_hud_{g.id}",
    )

    st.subheader("Building levels")
    yaml_levels = dict(g.buildings.levels)
    if g.buildings.furnace.level and "furnace" not in yaml_levels:
        yaml_levels["furnace"] = g.buildings.furnace.level
    _render_building_levels_table(
        _building_level_rows(yaml_levels),
        filter_key=f"player_state_bldg_yaml_{g.id}",
        empty_message="No building levels in ``db/state.yaml`` yet — sync from Century or run building OCR scenarios.",
    )

    st.subheader("Resources")
    snt.nested_table(
        [
            {
                "id": f"ps_res_{g.id}",
                "wood": g.resources.wood,
                "food": g.resources.food,
                "iron": g.resources.iron,
                "meat": g.resources.meat,
                "silver keys": g.resources.silver_keys,
                "gold keys": g.resources.gold_keys,
                "diamond": g.resources.diamond,
            }
        ],
        [
            snt.table_column(k, k, width=104, align="right")
            for k in _RESOURCES_COL_ORDER
        ],
        height=_player_state_table_height(1, 160),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_res_{g.id}",
    )

    st.subheader("Events · recruitment")
    snt.nested_table(
        [
            {
                "id": f"ps_recruit_{g.id}",
                "free recruitments today": g.events.recruitment.free_recruitments_today,
            }
        ],
        [
            snt.table_column(
                "free recruitments today",
                "free recruitments today",
                width=280,
                align="right",
            ),
        ],
        height=_player_state_table_height(1, 120),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_recruit_{g.id}",
    )

    st.subheader("Troops")
    snt.nested_table(
        [
            {
                "id": f"ps_troops_{g.id}",
                "infantry": g.troops.infantry.state.TextStatus or "—",
                "lancer": g.troops.lancer.state.TextStatus or "—",
                "marksman": g.troops.marksman.state.TextStatus or "—",
                "available infantry": bool(g.troops.infantry.state.isAvailable),
                "available lancer": bool(g.troops.lancer.state.isAvailable),
                "available marksman": bool(g.troops.marksman.state.isAvailable),
            }
        ],
        [
            snt.table_column("infantry", "infantry", width=168),
            snt.table_column("lancer", "lancer", width=168),
            snt.table_column("marksman", "marksman", width=168),
            snt.table_column(
                "available infantry",
                "avail inf",
                width=104,
                cell_type="bool",
            ),
            snt.table_column(
                "available lancer",
                "avail lnc",
                width=104,
                cell_type="bool",
            ),
            snt.table_column(
                "available marksman",
                "avail mm",
                width=104,
                cell_type="bool",
            ),
        ],
        height=_player_state_table_height(1, 160),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_troops_{g.id}",
    )

    st.subheader("Alliance / Exploration / Arena")
    snt.nested_table(
        [
            {
                "id": f"ps_alliance_{g.id}",
                "alliance": g.alliance.name or "—",
                "alliance power": g.alliance.power,
                "members": f"{g.alliance.members.count}/{g.alliance.members.max}",
                "exploration Lv": g.exploration.level,
                "exploration power": g.exploration.state.myPower,
                "arena rank": g.arena.rank,
                "arena power": g.arena.myPower,
                "contentment": g.chief.contentment,
            }
        ],
        [
            snt.table_column("alliance", "alliance", width=168),
            snt.table_column(
                "alliance power",
                "alliance power",
                width=120,
                align="right",
            ),
            snt.table_column("members", "members", width=96, align="center"),
            snt.table_column(
                "exploration Lv",
                "exploration Lv",
                width=120,
                align="right",
            ),
            snt.table_column(
                "exploration power",
                "exploration power",
                width=136,
                align="right",
            ),
            snt.table_column("arena rank", "arena rank", width=104, align="right"),
            snt.table_column("arena power", "arena power", width=120, align="right"),
            snt.table_column("contentment", "contentment", width=120),
        ],
        height=_player_state_table_height(1, 160),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_alliance_{g.id}",
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
    """Pull fresh player data from the Century API and persist it.

    Wrapped in ``st.status`` so the three independent steps (API fetch →
    state.yaml → devices.yaml) report progress live and a failure in any
    of them paints the container red without losing the steps that ran
    before. ``st.toast`` fires on the happy path so the user gets a
    non-blocking confirmation when the container collapses.
    """
    fid = int(g.id)
    now = time.time()

    with st.status(f"Syncing `{fid}` from Century API…", expanded=True) as status:
        st.write("Fetching player data…")
        try:
            data = asyncio.run(CenturyClient().fetch_player(fid))
        except CenturyAPIError as exc:
            status.update(label=f"Century API error: {exc}", state="error")
            return
        except Exception as exc:
            status.update(
                label=f"Century sync failed: {type(exc).__name__}: {exc}",
                state="error",
            )
            return
        st.write(
            f"Got `{data.nickname}` · stove `{data.stove_level}` · KID `{data.kid}`"
        )

        st.write("Persisting to `db/state.yaml`…")
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
            status.update(
                label=f"state.yaml persist failed: {type(exc).__name__}: {exc}",
                state="error",
            )
            return

        # ``devices.yaml`` upsert is best-effort — failure here doesn't
        # invalidate the Century data we just persisted, so we log the
        # skip but still mark the overall sync complete.
        try:
            iid = _infer_instance_id_for_player(str(fid))
            if iid:
                repo = default_repo_root()
                upsert_device_gamer(
                    path=repo / "db" / "devices.yaml",
                    device_name=iid,
                    player_id=str(fid),
                    nickname=data.nickname,
                )
                st.write(f"Linked instance `{iid}` ↔ player in `db/devices.yaml`.")
            else:
                st.write("No instance currently bound — skipped `devices.yaml`.")
        except Exception as exc:
            st.write(f"`devices.yaml` upsert skipped: {type(exc).__name__}: {exc}")

        status.update(
            label=f"Synced `{data.nickname}` · stove `{data.stove_level}` · "
            f"KID `{data.kid}` · fid `{fid}`",
            state="complete",
            expanded=False,
        )
    st.toast(f"Synced `{data.nickname}` from Century", icon="✅")
    st.rerun()


def _resolve_hero_icon(hero_id: str) -> Path | None:
    base = default_repo_root() / "db" / "assets" / "wiki" / "heroes" / hero_id
    if not base.is_dir():
        return None
    exts = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        return None
    files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
    return files[0]


@st.cache_data(ttl=3600)
def _icon_data_uri(path_str: str) -> str:
    """Inline a small icon as a base64 data: URI so it survives the
    cross-page <a href> jump without needing a Streamlit static-files route.

    Argument is a string (not :class:`Path`) so ``@st.cache_data`` can hash
    it cheaply — same trick :mod:`ui.views.wiki_db` uses."""
    raw = Path(path_str).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    suffix = Path(path_str).suffix.lower().lstrip(".")
    mime = (
        "image/png" if suffix == "png"
        else "image/webp" if suffix == "webp"
        else "image/gif" if suffix == "gif"
        else "image/jpeg"
    )
    return f"data:{mime};base64,{b64}"


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


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


def _wiki_hero_href(hero_id: str) -> str:
    """Root-relative link to the wiki entry for ``hero_id``.

    Matches the deep-link format ``ui/views/wiki_db.py`` reads in
    ``_render_index_tiles`` (``qparam_key="hero"``)."""
    return "/wiki_db?" + urlencode({"section": "heroes", "hero": hero_id})


def _wiki_building_url(building_id: str) -> str:
    return _internal_page_url(
        "wiki_db",
        {"section": "buildings", "building": building_id},
    )


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
    """Flatten ``heroes.entries`` into UI rows.

    ``available`` is the discriminator the panel uses to split Owned vs
    Locked. Default ``True`` keeps legacy entries written by
    ``sync_hero_unit`` (which doesn't set the flag) showing up under
    Owned — those were only written for cards the player actually opened.
    """
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
        try:
            shards_cur = int(raw.get("shards_current"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            shards_cur = 0
        try:
            shards_req = int(raw.get("shards_required"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            shards_req = 0
        available = raw.get("available")
        available_bool = True if available is None else bool(available)
        seen_ts = raw.get("seen_at")
        if seen_ts in (None, 0, "0"):
            seen_ts = raw.get("last_seen_at")
        rows.append(
            {
                "id": hid,
                "hero": name,
                "available": available_bool,
                "level": level,
                "shards_current": shards_cur,
                "shards_required": shards_req,
                "red_dot": bool(raw.get("red_dot")),
                "upgrade": bool(raw.get("isUpgradeAvailable")),
                "rarity": (hdef.rarity if hdef else "") or "—",
                "class": (hdef.hero_class if hdef else "") or "—",
                "sub_class": (hdef.sub_class if hdef else "") or "—",
                "seen": _format_seen_at(seen_ts),
            }
        )
    return rows


def _shard_progress(row: dict[str, object]) -> float:
    cur = int(row.get("shards_current") or 0)
    req = int(row.get("shards_required") or 0)
    return (cur / req) if req > 0 else 0.0


def _render_hero_tiles(
    rows: list[dict[str, object]],
    *,
    locked: bool,
    cols_per_row: int = 4,
) -> None:
    """Render a grid of hero tiles. ``locked`` swaps the caption from
    level + last-seen to shard progress + lock marker."""
    for i in range(0, len(rows), cols_per_row):
        chunk = rows[i : i + cols_per_row]
        tiles = st.columns(cols_per_row)
        for col, row in zip(tiles, chunk, strict=False):
            with col:
                hid = str(row["id"])
                icon = _resolve_hero_icon(hid)
                href = _wiki_hero_href(hid)
                if icon is not None and icon.is_file():
                    # Wrap the avatar in an <a> to /wiki_db so clicking the
                    # tile opens the wiki card. Image is inlined as a data:
                    # URI — Streamlit doesn't expose a static-files mount
                    # for arbitrary repo paths.
                    data_uri = _icon_data_uri(str(icon))
                    st.markdown(
                        f'<a href="{href}" title="Open wiki: {_html_escape(hid)}">'
                        f'<img src="{data_uri}" width="96" '
                        f'style="border-radius:8px;display:block" />'
                        f'</a>',
                        unsafe_allow_html=True,
                    )
                else:
                    # No local icon — still expose the wiki link via a tiny
                    # text fallback so the avatar slot stays clickable.
                    st.markdown(
                        f'<a href="{href}">🔗 wiki</a>',
                        unsafe_allow_html=True,
                    )
                tags: list[str] = []
                if row["red_dot"]:
                    tags.append(":red[●]")
                if row["upgrade"]:
                    tags.append(":green[↑]")
                tag_str = (" " + " ".join(tags)) if tags else ""
                st.markdown(f"**{row['hero']}** · `{row['id']}`{tag_str}")
                if locked:
                    cur = int(row["shards_current"])
                    req = int(row["shards_required"])
                    if req > 0:
                        st.caption(f"🔒 shards {cur}/{req}")
                    else:
                        st.caption("🔒 locked")
                    st.caption(
                        f"{row['rarity']} · {row['class']} / {row['sub_class']}"
                    )
                else:
                    st.caption(
                        f"Lv. {row['level']} · {row['rarity']} · "
                        f"{row['class']} / {row['sub_class']}"
                    )
                    st.caption(f"seen: {row['seen']}")


def _render_heroes_panel(g: GamerState) -> None:
    entries_raw = g.heroes.entries or {}
    entries: dict[str, object] = {
        str(k): v for k, v in entries_raw.items() if isinstance(v, dict)
    }
    reg = get_hero_registry()
    total_registry = len(reg.heroes)
    rows_all = _hero_entries_rows(entries)
    owned_count = sum(1 for r in rows_all if r["available"])
    locked_count = len(rows_all) - owned_count

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Owned", owned_count)
    c2.metric("Locked / collecting", locked_count)
    c3.metric("In registry", total_registry)
    c4.metric("Notify", "yes" if g.heroes.isnotify else "no")

    if not entries:
        st.info(
            "No hero snapshots yet for this player. Open a hero card in-game "
            "and run the `sync_hero_unit` scenario — entries land in "
            "`heroes.entries.<id>` of `db/state.yaml`."
        )
    else:
        show_filter = st.text_input(
            "Filter (name / id / class)",
            value="",
            key=f"player_state_heroes_filter_{g.id}",
        ).strip().lower()

        def _passes_filter(r: dict[str, object]) -> bool:
            if not show_filter:
                return True
            hay = " ".join(str(v) for v in r.values()).lower()
            return show_filter in hay

        owned_rows = sorted(
            (r for r in rows_all if r["available"]),
            key=lambda r: (-int(r["level"] or 0), str(r["hero"]).lower()),
        )
        locked_rows = sorted(
            (r for r in rows_all if not r["available"]),
            # Closest-to-unlock first; ties broken by name.
            key=lambda r: (-_shard_progress(r), str(r["hero"]).lower()),
        )
        owned_visible = [r for r in owned_rows if _passes_filter(r)]
        locked_visible = [r for r in locked_rows if _passes_filter(r)]

        if not owned_visible and not locked_visible:
            st.info("No heroes matched the current filter.")

        if owned_visible:
            st.subheader(f"Owned heroes ({len(owned_visible)}/{len(owned_rows)})")
            _render_hero_tiles(owned_visible, locked=False)
            st.divider()
            st.markdown("**Owned · table view**")
            snt.nested_table(
                _load_owned_hero_nested_rows(owned_visible),
                _owned_heroes_nested_columns(),
                height=_player_state_table_height(len(owned_visible), 420),
                striped=True,
                compact=True,
                hide_expand=True,
                key=f"player_state_heroes_owned_{g.id}",
            )
        elif owned_rows:
            st.caption(
                f"No owned heroes matched the filter "
                f"(hiding {len(owned_rows)} owned)."
            )

        if locked_visible:
            st.subheader(
                f"Locked · collecting shards "
                f"({len(locked_visible)}/{len(locked_rows)})"
            )
            _render_hero_tiles(locked_visible, locked=True)
            st.divider()
            st.markdown("**Locked · table view**")
            snt.nested_table(
                _load_locked_hero_nested_rows(locked_visible),
                _locked_heroes_nested_columns(),
                height=_player_state_table_height(len(locked_visible), 420),
                striped=True,
                compact=True,
                hide_expand=True,
                key=f"player_state_heroes_locked_{g.id}",
            )
        elif locked_rows:
            st.caption(
                f"No locked heroes matched the filter "
                f"(hiding {len(locked_rows)} locked)."
            )

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
            snt.nested_table(
                missing_rows,
                _missing_heroes_nested_columns(),
                height=_player_state_table_height(len(missing_rows), 360),
                striped=True,
                compact=True,
                hide_expand=True,
                key=f"player_state_heroes_missing_{g.id}",
            )

    with st.expander("Raw `heroes.entries`", expanded=False):
        st.json(entries_raw)


def _building_level_rows(levels: dict[str, int | str]) -> list[dict[str, object]]:
    reg = get_building_registry()
    rows: list[dict[str, object]] = []
    for bid_raw, val in levels.items():
        bid = str(bid_raw).strip()
        if not bid:
            continue
        try:
            lv: int | str = int(str(val).strip())
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
                "wiki": _wiki_building_url(bid),
            }
        )
    rows.sort(key=lambda r: str(r["building"]).lower())
    return rows


def _building_level_rows_from_redis(state: dict[str, str]) -> list[dict[str, object]]:
    levels: dict[str, str] = {}
    for key, val in state.items():
        if not key.startswith(_LEVEL_PREFIX):
            continue
        bid = key[len(_LEVEL_PREFIX) :].strip()
        if bid:
            levels[bid] = val
    return _building_level_rows(levels)


def _render_building_levels_table(
    rows: list[dict[str, object]],
    *,
    filter_key: str,
    empty_message: str,
) -> None:
    if not rows:
        st.info(empty_message)
        return

    filt = st.text_input(
        "Filter (building / id / category)",
        value="",
        key=filter_key,
    ).strip().lower()

    visible = rows
    if filt:
        visible = [
            r
            for r in rows
            if filt
            in " ".join(
                str(r.get(k) or "")
                for k in ("id", "building", "category", "level")
            ).lower()
        ]

    m1, m2 = st.columns(2)
    m1.metric("Buildings tracked", len(rows))
    numeric = [int(r["level"]) for r in rows if isinstance(r.get("level"), int)]
    m2.metric("Highest level", max(numeric) if numeric else "—")

    if not visible:
        st.info("No buildings matched the filter.")
        return

    view_rows = [{**dict(r), "wiki_label": "Wiki"} for r in visible]
    snt.nested_table(
        view_rows,
        _building_levels_nested_columns(),
        height=_player_state_table_height(len(visible), 480),
        striped=True,
        compact=True,
        hide_expand=True,
        key=f"player_state_bldg_levels_{filter_key}",
    )


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

repo = default_repo_root()
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
    _render_building_levels_table(
        _building_level_rows_from_redis(state),
        filter_key=f"player_state_bldg_redis_{pid}",
        empty_message=(
            "No ``buildings.levels.*`` keys yet — wait for ``fetch_player`` "
            "or building sync from the bot."
        ),
    )


# Two-way ``?tab=`` routing via the native st.tabs API
# (Streamlit 1.57+ — ``default=``, ``key=``, ``on_change=`` are supported).
# Deep links like ``/player_state?tab=heroes`` land on the right tab;
# user clicks fire ``_on_player_state_tab_change`` which echoes the new
# key back into ``st.query_params`` so the URL stays in sync (incl. for
# share / refresh / browser back-forward).
_TAB_LABEL: dict[str, str] = {
    "redis": "Redis (live)",
    "yaml": "Persisted (state.yaml)",
    "heroes": "Heroes",
}
_LABEL_TO_TAB: dict[str, str] = {v: k for k, v in _TAB_LABEL.items()}


def _query_param_tab() -> str:
    raw = st.query_params.get("tab")
    s = raw[0] if isinstance(raw, list) and raw else (raw or "")
    s = str(s).strip().lower()
    return s if s in _TAB_LABEL else "redis"


def _on_player_state_tab_change() -> None:
    label = st.session_state.get("player_state_tabs")
    key = _LABEL_TO_TAB.get(label or "")
    if key and key != _query_param_tab():
        st.query_params["tab"] = key


# Pull the URL value into session_state BEFORE the widget renders so
# browser back/forward (and manual ``?tab=`` edits) drive the active
# tab. ``default=`` alone wouldn't help here — once the widget has been
# touched, ``session_state[key]`` takes precedence over ``default=``.
_url_label = _TAB_LABEL[_query_param_tab()]
if st.session_state.get("player_state_tabs") != _url_label:
    st.session_state["player_state_tabs"] = _url_label

tab_redis, tab_yaml, tab_heroes = st.tabs(
    list(_TAB_LABEL.values()),
    default=_url_label,
    key="player_state_tabs",
    on_change=_on_player_state_tab_change,
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
