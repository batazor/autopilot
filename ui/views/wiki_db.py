"""DB: wiki-derived reference data (buildings, heroes, items) + FAQ."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
import yaml

_SECTION_LABEL: dict[str, str] = {
    "buildings": "Buildings",
    "heroes": "Heroes",
    "gear": "Gear",
    "items": "Items",
    "faq": "FAQ",
}
_LABEL_TO_SECTION: dict[str, str] = {v: k for k, v in _SECTION_LABEL.items()}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


@st.cache_data(ttl=60)
def _load_index_cached(path_s: str, mtime_ns: int, size: int) -> dict[str, Any]:
    _ = (mtime_ns, size)
    return _load_yaml_dict(Path(path_s))


def _load_index(path: Path) -> dict[str, Any]:
    try:
        stt = path.stat()
    except OSError:
        return {}
    return _load_index_cached(str(path), stt.st_mtime_ns, stt.st_size)


def _render_wiki_link(wiki_url: str) -> None:
    u = wiki_url.strip()
    if not u:
        return
    st.markdown(f"**Wiki:** [{u}]({u})")


def _render_building(building: dict[str, Any]) -> None:
    st.subheader(f"{building.get('name') or '(unnamed)'} · `{building.get('id') or ''}`")
    wiki_url = str(building.get("wiki_url") or "").strip()
    if wiki_url:
        _render_wiki_link(wiki_url)

    req = building.get("requirements_by_level")
    if isinstance(req, dict) and req:
        rows: list[dict[str, object]] = []
        for lvl_s, row in req.items():
            try:
                lvl = int(lvl_s)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            cost = row.get("build_cost")
            cost_s = ""
            if isinstance(cost, list):
                parts: list[str] = []
                for ci in cost:
                    if not isinstance(ci, dict):
                        continue
                    item = str(ci.get("item") or "").strip()
                    amount = str(ci.get("amount") or "").strip()
                    if item and amount:
                        parts.append(f"{item}:{amount}")
                cost_s = ", ".join(parts)
            rows.append(
                {
                    "level": lvl,
                    "prerequisites": str(row.get("prerequisites") or ""),
                    "build_cost": cost_s,
                    "construction_time": str(row.get("construction_time") or ""),
                    "building_power": row.get("building_power"),
                }
            )
        if rows:
            df = pd.DataFrame(sorted(rows, key=lambda r: int(r["level"])))  # type: ignore[arg-type]
            st.markdown("**Requirements**")
            st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No `requirements_by_level` for this building (wiki page may not have a table).")


def _render_hero(hero: dict[str, Any]) -> None:
    st.subheader(f"{hero.get('name') or '(unnamed)'} · `{hero.get('id') or ''}`")
    wiki_url = str(hero.get("wiki_url") or "").strip()
    if wiki_url:
        _render_wiki_link(wiki_url)

    meta = []
    for k in ("rarity", "class", "sub_class"):
        v = str(hero.get(k) or "").strip()
        if v:
            meta.append(f"**{k}**: {v}")
    if meta:
        st.markdown(" · ".join(meta))

    story = str(hero.get("story") or "").strip()
    if story:
        with st.expander("Story", expanded=False):
            st.write(story)

    stats = hero.get("stats")
    if isinstance(stats, dict) and stats:
        st.markdown("**Stats**")
        st.json(stats)

    skills = hero.get("skills")
    if isinstance(skills, list) and skills:
        with st.expander(f"Skills ({len(skills)})", expanded=False):
            for s in skills:
                if not isinstance(s, dict):
                    continue
                nm = str(s.get("name") or "").strip() or "(unnamed)"
                desc = str(s.get("description") or "").strip()
                st.markdown(f"- **{nm}**")
                if desc:
                    st.caption(desc)
    else:
        st.info("No skills parsed for this hero yet.")

    levels = hero.get("levels")
    if isinstance(levels, dict) and isinstance(levels.get("table"), dict):
        _render_hero_levels(levels)


def _render_hero_levels(levels: dict[str, Any]) -> None:
    """Per-level table from ``cmd/sync_balance_sheet.py`` — stats × levels."""
    table = levels.get("table") or {}
    if not table:
        return
    try:
        keys = sorted(int(k) for k in table)
    except (TypeError, ValueError):
        return
    # Collect every stat name across all levels — heroes occasionally pick
    # up a stat at a later level (e.g. ``skill_2`` arrives at L2).
    stat_names: list[str] = []
    seen: set[str] = set()
    for lv in keys:
        row = table.get(lv) or table.get(str(lv)) or {}
        if not isinstance(row, dict):
            continue
        for stat in row:
            if stat not in seen:
                seen.add(stat)
                stat_names.append(stat)
    matrix: dict[str, list[Any]] = {f"L{lv}": [] for lv in keys}
    for stat in stat_names:
        for lv in keys:
            row = table.get(lv) or table.get(str(lv)) or {}
            matrix[f"L{lv}"].append(row.get(stat) if isinstance(row, dict) else None)
    df = pd.DataFrame(matrix, index=stat_names)
    df.index.name = "stat"
    st.markdown("**Levels**")
    st.dataframe(df, width="stretch")
    src = levels.get("source")
    if isinstance(src, dict):
        gid = src.get("gid")
        fetched = src.get("fetched_at")
        if gid and fetched:
            st.caption(f"source: google sheet · gid={gid} · fetched {fetched}")


def _render_gear(gear: dict[str, Any]) -> None:
    """One gear file (e.g. ``goggles_boots_marksman.yaml``) — per-tier
    level tables stacked side-by-side."""
    title = str(gear.get("title") or gear.get("id") or "(unnamed)").strip()
    st.subheader(f"{title} · `{gear.get('id') or ''}`")

    meta = []
    for k in ("slot", "troop_class"):
        v = str(gear.get(k) or "").strip()
        if v:
            meta.append(f"**{k}**: {v}")
    stat_names = gear.get("stats")
    tier_names = gear.get("tiers")
    if isinstance(stat_names, list) and stat_names:
        meta.append("**stats**: " + ", ".join(stat_names))
    if isinstance(tier_names, list) and tier_names:
        meta.append("**tiers**: " + ", ".join(tier_names))
    if meta:
        st.markdown(" · ".join(meta))

    levels = gear.get("levels")
    if not (isinstance(levels, dict) and levels):
        st.info("No level data for this gear yet.")
        return
    try:
        lv_keys = sorted(int(k) for k in levels)
    except (TypeError, ValueError):
        return
    stat_list = (
        list(stat_names) if isinstance(stat_names, list) and stat_names else []
    )
    tier_list = list(tier_names) if isinstance(tier_names, list) else _TIERS_DEFAULT

    tab_labels = [t.capitalize() for t in tier_list]
    tabs = st.tabs(tab_labels)
    for tier, tab in zip(tier_list, tabs, strict=False):
        with tab:
            rows: list[dict[str, Any]] = []
            for lv in lv_keys:
                entry = levels.get(lv) or levels.get(str(lv)) or {}
                if not isinstance(entry, dict):
                    continue
                tier_entry = entry.get(tier)
                if not isinstance(tier_entry, dict):
                    continue
                row = {"level": lv}
                for stat in stat_list:
                    row[stat] = tier_entry.get(stat)
                rows.append(row)
            if rows:
                df = pd.DataFrame(rows).set_index("level")
                st.dataframe(df, width="stretch")
            else:
                st.caption(f"No values at tier `{tier}` (unreachable on this slot).")

    src = gear.get("source")
    if isinstance(src, dict):
        gid = src.get("gid")
        fetched = src.get("fetched_at")
        if gid and fetched:
            st.caption(f"source: google sheet · gid={gid} · fetched {fetched}")


_TIERS_DEFAULT = ["grey", "green", "blue", "purple", "gold"]


def _render_enhancement(enh: dict[str, Any]) -> None:
    """``db/gear/enhancement.yaml`` — multi-section constants table."""
    st.subheader("Gear enhancement constants")

    pts = enh.get("points_required")
    if isinstance(pts, dict) and pts:
        st.markdown("**Points required to reach gear level**")
        rows: dict[int, dict[str, Any]] = {}
        for tier, by_lv in pts.items():
            if not isinstance(by_lv, dict):
                continue
            for lv, v in by_lv.items():
                try:
                    lv_i = int(lv)
                except (TypeError, ValueError):
                    continue
                rows.setdefault(lv_i, {})[tier] = v
        if rows:
            df = pd.DataFrame(rows).T.sort_index()
            df.index.name = "level"
            st.dataframe(df, width="stretch")
        totals = enh.get("points_required_totals")
        if isinstance(totals, dict) and totals:
            st.caption("Totals (sum of all levels): " + ", ".join(
                f"{t}={totals[t]}" for t in totals
            ))

    sac = enh.get("points_per_tier_sacrifice")
    if isinstance(sac, dict) and sac:
        st.markdown("**Points gained when sacrificing a gear piece**")
        st.json(sac)

    mythic = enh.get("mythic_max_costs")
    if isinstance(mythic, dict) and mythic:
        st.markdown("**Cost to max a mythic piece (level 100)**")
        rows_m: list[dict[str, Any]] = []
        for lv in sorted(int(k) for k in mythic):
            entry = dict(mythic[lv])
            entry["from_lv"] = lv
            rows_m.append(entry)
        if rows_m:
            df = pd.DataFrame(rows_m).set_index("from_lv")
            st.dataframe(df, width="stretch")

    mastery = enh.get("mastery_levels")
    if isinstance(mastery, dict) and mastery:
        st.markdown("**Mastery levels**")
        rows_l: list[dict[str, Any]] = []
        for lv in sorted(int(k) for k in mastery):
            entry = dict(mastery[lv])
            entry["mastery_level"] = lv
            rows_l.append(entry)
        if rows_l:
            df = pd.DataFrame(rows_l).set_index("mastery_level")
            st.dataframe(df, width="stretch")

    widgets = enh.get("weapon_widgets")
    if isinstance(widgets, dict) and widgets:
        st.markdown("**Weapon widgets per level**")
        rows_w: list[dict[str, Any]] = []
        total = None
        for k, v in widgets.items():
            try:
                rows_w.append({"level": int(k), "widgets": v})
            except (TypeError, ValueError):
                if str(k).lower() == "total":
                    total = v
        if rows_w:
            df = pd.DataFrame(rows_w).set_index("level").sort_index()
            st.dataframe(df, width="stretch")
        if total is not None:
            st.caption(f"Total widgets across all levels: **{total}**")

    src = enh.get("source")
    if isinstance(src, dict):
        gid = src.get("gid")
        fetched = src.get("fetched_at")
        if gid is not None and fetched:
            st.caption(f"source: google sheet · gid={gid} · fetched {fetched}")


def _render_item(item: dict[str, Any]) -> None:
    st.subheader(f"{item.get('name') or '(unnamed)'} · `{item.get('id') or ''}`")
    wiki_url = str(item.get("wiki_url") or "").strip()
    if wiki_url:
        _render_wiki_link(wiki_url)

    desc = str(item.get("description") or "").strip()
    if desc:
        st.markdown("**Description**")
        st.write(desc)

    sources = item.get("sources")
    if isinstance(sources, list) and sources:
        st.markdown("**Sources**")
        for s in sources:
            if isinstance(s, str) and s.strip():
                st.markdown(f"- {s.strip()}")


def _select_from_index(
    index: dict[str, Any],
    key: str,
    label: str,
    *,
    search_query: str,
) -> dict[str, Any] | None:
    entries = index.get(key)
    if not isinstance(entries, list):
        st.error(f"Invalid index format: `{key}` is not a list.")
        return None

    q = search_query.strip().lower()
    filtered: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "")
        eid = str(e.get("id") or "")
        hay = f"{name} {eid}".lower()
        if q and q not in hay:
            continue
        filtered.append(e)

    if not filtered:
        st.info("No matches.")
        return None

    def _fmt(e: dict[str, Any]) -> str:
        return f"{e.get('name') or '—'} · {e.get('id') or '—'}"

    selected = st.selectbox(label, options=filtered, format_func=_fmt, key=f"wiki_db_sel_{key}")
    if not isinstance(selected, dict):
        return None
    return selected


def _entity_yaml_path(dir_path: Path, entry: dict[str, Any]) -> Path | None:
    """Resolved path to entity YAML, or None if path cannot be determined."""
    file_rel = str(entry.get("file") or "").strip()
    if not file_rel:
        eid = str(entry.get("id") or "").strip()
        file_rel = f"{eid}.yaml" if eid else ""
    if not file_rel:
        return None
    return (dir_path / file_rel).resolve()


def _get_qparam_str(key: str) -> str:
    raw = st.query_params.get(key)
    s = raw[0] if isinstance(raw, list) and raw else (raw or "")
    return str(s or "").strip()


def _set_qparam(key: str, value: str) -> None:
    st.query_params[key] = str(value)
    st.rerun()


def _page_for_selected_id(
    filtered: list[dict[str, Any]], selected_id: str, page_size: int, max_page: int
) -> int:
    if not selected_id:
        return 1
    for i, e in enumerate(filtered):
        if not isinstance(e, dict):
            continue
        if str(e.get("id") or "").strip() == selected_id:
            return min(max_page, i // page_size + 1)
    return 1


def _render_index_tiles(
    *,
    index: dict[str, Any],
    index_key: str,
    label: str,
    section_name: str,
    qparam_key: str,
    search_query: str,
    icon_prefix: str | None = None,
    cols: int = 4,
    page_size: int = 40,
) -> dict[str, Any] | None:
    entries = index.get(index_key)
    if not isinstance(entries, list):
        st.error(f"Invalid index format: `{index_key}` is not a list.")
        return None

    q = search_query.strip().lower()
    filtered: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "")
        eid = str(e.get("id") or "")
        hay = f"{name} {eid}".lower()
        if q and q not in hay:
            continue
        filtered.append(e)

    if not filtered:
        st.info("No matches.")
        return None

    selected_id = _get_qparam_str(qparam_key)

    total = len(filtered)
    max_page = max(1, (total + page_size - 1) // page_size)
    page_default = _page_for_selected_id(filtered, selected_id, page_size, max_page)
    page_widget_key = f"wiki_db_tiles_page_{index_key}_{selected_id or '__none__'}"
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=max_page,
        value=page_default,
        step=1,
        key=page_widget_key,
    )
    start = (int(page) - 1) * page_size
    chunk = filtered[start : start + page_size]

    st.caption(f"{label}: showing {start + 1}-{min(start + len(chunk), total)} of {total}")

    # Render tiles
    grid = st.columns(cols)
    for i, e in enumerate(chunk):
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "—").strip() or "—"
        eid = str(e.get("id") or "").strip()
        btn_label = name if len(name) <= 28 else (name[:28] + "…")
        with grid[i % cols]:
            if icon_prefix and eid:
                icon = _resolve_local_icon(icon_prefix, eid)
                if icon is not None:
                    href = "?" + urlencode({"section": section_name, qparam_key: eid})
                    data_uri = _icon_data_uri(icon)
                    st.markdown(
                        f"""
<a href="{href}" style="text-decoration:none">
  <div style="
    border: 1px solid rgba(49, 51, 63, 0.2);
    border-radius: 12px;
    padding: 12px;
    height: 128px;
    display: flex;
    gap: 12px;
    align-items: center;
    background: rgba(255,255,255,0.02);
  ">
    <img src="{data_uri}" style="width:64px;height:64px;object-fit:contain" />
    <div style="font-size:14px;line-height:1.2;color:inherit">
      <div style="font-weight:600">{_html_escape(btn_label)}</div>
      <div style="opacity:0.65;font-size:12px">{_html_escape(eid)}</div>
    </div>
  </div>
</a>
""",
                        unsafe_allow_html=True,
                    )
                    continue

            # Fallback: text-only tile
            tile_key = f"tile_{index_key}_{eid}_{start + i}"
            if st.button(btn_label, use_container_width=True, key=tile_key):
                _set_qparam(qparam_key, eid)

    if selected_id:
        for e in filtered:
            if str(e.get("id") or "").strip() == selected_id:
                return e
    return None


def _render_faq() -> None:
    st.subheader("FAQ")

    st.markdown("**Where does the data come from?**")
    st.write(
        "We scrape and store wiki pages into `db/` YAML files. "
        "The primary sources are the Wiki index pages and per-entity pages."
    )

    st.markdown("**How to refresh the data?**")

    repo = _repo_root()

    def _run_script(
        *,
        title: str,
        script_rel: str,
        args: list[str] | None = None,
        progress_total_hint: int | None = None,
    ) -> None:
        args = args or []
        script = (repo / script_rel).resolve()
        if not script.is_file():
            st.error(f"Script not found: `{script_rel}`")
            return

        st.markdown(f"**{title}**")
        pb = st.progress(0)
        log = st.empty()
        stats = st.empty()

        cmd = [sys.executable, str(script), *args]
        started = time.time()

        output_lines: list[str] = []
        done = 0
        total = progress_total_hint or 0
        extracted_summary: str = ""

        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            st.error(f"Failed to start process: {type(exc).__name__}: {exc}")
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            output_lines.append(line)
            # Keep the last ~200 lines in UI to avoid huge payloads.
            tail = output_lines[-200:]
            log.code("\n".join(tail), language="text")

            # Generic progress parser: "progress: 25/405 (ok=...)"
            m = re.search(r"progress:\s*(\d+)\s*/\s*(\d+)", line)
            if m:
                done = int(m.group(1))
                total = int(m.group(2))
                if total > 0:
                    pb.progress(min(1.0, done / total))

            # Summary extractor
            if line.startswith("updated ") or line.startswith("downloaded "):
                extracted_summary = line

        rc = proc.wait()
        elapsed = time.time() - started

        # Final progress state
        if total > 0:
            pb.progress(1.0 if rc == 0 else min(1.0, done / total))
        else:
            pb.progress(1.0 if rc == 0 else 0.0)

        summary = extracted_summary or "(no summary line found)"
        stats.markdown(
            "\n".join(
                [
                    f"**Result**: `exit_code={rc}` · **elapsed**: `{elapsed:.1f}s`",
                    f"**Summary**: `{summary}`",
                    f"**Command**: `{json.dumps(cmd)}`",
                ]
            )
        )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Sync buildings", use_container_width=True):
            _run_script(title="Sync buildings", script_rel="cmd/sync_buildings_wiki.py")
        if st.button("Sync heroes", use_container_width=True):
            _run_script(title="Sync heroes", script_rel="cmd/sync_heroes_wiki.py")
    with c2:
        if st.button("Sync items", use_container_width=True):
            # items script prints progress N/405, so we can show a real progress bar.
            _run_script(
                title="Sync items",
                script_rel="cmd/sync_items_wiki.py",
                progress_total_hint=405,
            )
        if st.button("Download images (all)", use_container_width=True):
            _run_script(
                title="Download wiki images (all)",
                script_rel="cmd/download_wiki_images.py",
                args=["all"],
            )
        if st.button("Sync balance sheet", use_container_width=True):
            _run_script(
                title="Sync balance sheet (heroes levels + gear + enhancement)",
                script_rel="cmd/sync_balance_sheet.py",
            )

    st.markdown("**Why are some building requirements empty?**")
    st.write(
        "Some building pages do not have a `Requirements` table on the wiki, so we cannot "
        "extract level/cost/time data from them."
    )

    st.markdown("**What is `item_icon_XXX` in build costs?**")
    st.write(
        "The wiki often represents resource types as icons in tables. We store the icon filename "
        "as a stable identifier (e.g. `item_icon_103`) until we add a mapping to canonical names."
    )

    st.markdown("**Local assets**")
    st.write("Downloaded images are stored under `db/assets/wiki/`.")


def _resolve_local_icon(prefix: str, entity_id: str) -> Path | None:
    """Best-effort local icon lookup under db/assets/wiki/<prefix>/<id>/."""
    base = _repo_root() / "db" / "assets" / "wiki" / prefix / entity_id
    if not base.is_dir():
        return None
    # Prefer common image types; pick first stable-sorted.
    exts = {".png", ".webp", ".jpg", ".jpeg", ".gif"}
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        return None
    files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
    return files[0]


@st.cache_data(ttl=3600)
def _icon_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    ext = path.suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else ("image/webp" if ext == "webp" else "image/jpeg")
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


def _get_section() -> str:
    raw = st.query_params.get("section")
    s = raw[0] if isinstance(raw, list) and raw else (raw or "")
    s = str(s).strip().lower()
    return s or "home"


def _url_section_key() -> str:
    s = _get_section()
    if s in _SECTION_LABEL:
        return s
    return "buildings"


st.title("DB · Wiki reference")
st.caption("Buildings, heroes, items and FAQ — sourced from whiteoutsurvival.wiki.")

repo = _repo_root()

if "wiki_db_panel_radio" not in st.session_state:
    st.session_state["wiki_db_panel_radio"] = _SECTION_LABEL[_url_section_key()]

label_from_url = _SECTION_LABEL[_url_section_key()]
if st.session_state["wiki_db_panel_radio"] != label_from_url:
    st.session_state["wiki_db_panel_radio"] = label_from_url


def _on_panel_radio_change() -> None:
    lab = st.session_state.get("wiki_db_panel_radio")
    if lab and lab in _LABEL_TO_SECTION:
        st.query_params["section"] = _LABEL_TO_SECTION[lab]


st.radio(
    "Section",
    options=list(_SECTION_LABEL.values()),
    horizontal=True,
    key="wiki_db_panel_radio",
    on_change=_on_panel_radio_change,
    label_visibility="collapsed",
)
panel = _LABEL_TO_SECTION[st.session_state["wiki_db_panel_radio"]]

if _get_section() == "home":
    st.caption(
        "URL has no `section` — showing **Buildings**. "
        "Use the row above or `?section=heroes` etc."
    )

if panel == "buildings":
    idx = _load_index(repo / "db" / "buildings" / "index.yaml")
    cset1, cset2, cset3 = st.columns([1, 1, 2], vertical_alignment="center")
    with cset1:
        cols = st.select_slider(
            "Grid",
            options=[2, 3, 4, 5],
            value=3,
            key="wiki_db_buildings_grid_cols",
        )
    with cset2:
        page_size = st.select_slider(
            "Page size",
            options=[18, 24, 36, 48, 60],
            value=36,
            key="wiki_db_buildings_grid_page_size",
        )
    with cset3:
        st.caption("Tiles are clickable cards (image + text) when an icon is available.")

    q = st.text_input(
        "Search",
        value="",
        key="wiki_db_search_buildings",
        help="Filters tiles and the list selector below.",
    ).strip()

    sel = _render_index_tiles(
        index=idx,
        index_key="buildings",
        label="Buildings",
        section_name="buildings",
        qparam_key="building",
        search_query=q,
        icon_prefix="buildings",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click a building tile to open its card.")
        sel = _select_from_index(idx, "buildings", "Building", search_query=q)
    if sel:
        bdir = repo / "db" / "buildings"
        ypath = _entity_yaml_path(bdir, sel)
        if ypath is None:
            st.warning("Cannot resolve YAML path for this index entry (missing `id` / `file`).")
        elif not ypath.is_file():
            st.warning(f"No YAML file at `{ypath}`. Run **Sync buildings** in FAQ.")
        else:
            building = _load_yaml_dict(ypath)
            if not building:
                st.warning(f"Empty or invalid YAML at `{ypath}`.")
            else:
                _render_building(building)

elif panel == "heroes":
    idx = _load_index(repo / "db" / "heroes" / "index.yaml")
    cset1, cset2, cset3 = st.columns([1, 1, 2], vertical_alignment="center")
    with cset1:
        cols = st.select_slider(
            "Grid",
            options=[2, 3, 4, 5, 6],
            value=4,
            key="wiki_db_heroes_grid_cols",
        )
    with cset2:
        page_size = st.select_slider(
            "Page size",
            options=[24, 36, 48, 60, 72, 96],
            value=48,
            key="wiki_db_heroes_grid_page_size",
        )
    with cset3:
        st.caption("Tiles are clickable cards (image + text) when an icon is available.")

    q = st.text_input(
        "Search",
        value="",
        key="wiki_db_search_heroes",
        help="Filters tiles and the list selector below.",
    ).strip()

    sel = _render_index_tiles(
        index=idx,
        index_key="heroes",
        label="Heroes",
        section_name="heroes",
        qparam_key="hero",
        search_query=q,
        icon_prefix="heroes",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click a hero tile to open its card.")
        sel = _select_from_index(idx, "heroes", "Hero", search_query=q)
    if sel:
        hdir = repo / "db" / "heroes"
        ypath = _entity_yaml_path(hdir, sel)
        if ypath is None:
            st.warning("Cannot resolve YAML path for this index entry (missing `id` / `file`).")
        elif not ypath.is_file():
            st.warning(f"No YAML file at `{ypath}`. Run **Sync heroes** in FAQ.")
        else:
            hero = _load_yaml_dict(ypath)
            if not hero:
                st.warning(f"Empty or invalid YAML at `{ypath}`.")
            else:
                _render_hero(hero)

elif panel == "gear":
    gear_dir = repo / "db" / "gear"
    if not gear_dir.is_dir():
        st.warning("No `db/gear/` directory yet. Run **Sync balance sheet** in FAQ.")
    else:
        gear_files = sorted(
            p for p in gear_dir.glob("*.yaml")
            if p.is_file() and p.name != "enhancement.yaml"
        )
        if not gear_files:
            st.info("No gear files yet. Run **Sync balance sheet** in FAQ.")
        else:
            options: list[tuple[str, Path]] = []
            for p in gear_files:
                doc = _load_yaml_dict(p)
                title = str(doc.get("title") or doc.get("id") or p.stem)
                options.append((title, p))
            options.append(("Enhancement (shared constants)", gear_dir / "enhancement.yaml"))

            labels = [o[0] for o in options]
            picked = st.radio(
                "Gear table",
                options=labels,
                horizontal=False,
                key="wiki_db_gear_radio",
                label_visibility="collapsed",
            )
            target = next(p for label, p in options if label == picked)
            doc = _load_yaml_dict(target)
            if not doc:
                st.warning(f"Empty or invalid YAML at `{target}`.")
            elif target.name == "enhancement.yaml":
                _render_enhancement(doc)
            else:
                _render_gear(doc)

elif panel == "items":
    idx = _load_index(repo / "db" / "items" / "index.yaml")
    cset1, cset2, cset3 = st.columns([1, 1, 2], vertical_alignment="center")
    with cset1:
        cols = st.select_slider(
            "Grid",
            options=[2, 3, 4, 5, 6],
            value=4,
            key="wiki_db_items_grid_cols",
        )
    with cset2:
        page_size = st.select_slider(
            "Page size",
            options=[24, 36, 48, 60, 72, 96],
            value=48,
            key="wiki_db_items_grid_page_size",
        )
    with cset3:
        st.caption("Tiles are clickable cards (image + text).")

    q = st.text_input(
        "Search",
        value="",
        key="wiki_db_search_items",
        help="Filters tiles and the list selector below.",
    ).strip()

    sel = _render_index_tiles(
        index=idx,
        index_key="items",
        label="Items",
        section_name="items",
        qparam_key="item",
        search_query=q,
        icon_prefix="items",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click an item tile to open its card.")
        sel = _select_from_index(idx, "items", "Item", search_query=q)
    if sel:
        idir = repo / "db" / "items"
        ypath = _entity_yaml_path(idir, sel)
        if ypath is None:
            st.warning("Cannot resolve YAML path for this index entry (missing `id` / `file`).")
        elif not ypath.is_file():
            st.warning(f"No YAML file at `{ypath}`. Run **Sync items** in FAQ.")
        else:
            item = _load_yaml_dict(ypath)
            if not item:
                st.warning(f"Empty or invalid YAML at `{ypath}`.")
            else:
                _render_item(item)

else:
    _render_faq()

