"""DB: wiki-derived reference data (buildings, heroes, items) + FAQ."""

from __future__ import annotations

import json
import base64
import subprocess
import sys
import time
import re
from urllib.parse import urlencode
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml


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


def _render_building(building: dict[str, Any]) -> None:
    st.subheader(f"{building.get('name') or '(unnamed)'} · `{building.get('id') or ''}`")
    wiki_url = str(building.get("wiki_url") or "").strip()
    if wiki_url:
        st.markdown(f"**Wiki:** `{wiki_url}`")

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
        st.markdown(f"**Wiki:** `{wiki_url}`")

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
        st.markdown("**Skills**")
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


def _render_item(item: dict[str, Any]) -> None:
    st.subheader(f"{item.get('name') or '(unnamed)'} · `{item.get('id') or ''}`")
    wiki_url = str(item.get("wiki_url") or "").strip()
    if wiki_url:
        st.markdown(f"**Wiki:** `{wiki_url}`")

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


def _select_from_index(index: dict[str, Any], key: str, label: str) -> dict[str, Any] | None:
    entries = index.get(key)
    if not isinstance(entries, list):
        st.error(f"Invalid index format: `{key}` is not a list.")
        return None

    q = st.text_input("Search", value="", key=f"wiki_db_search_{key}").strip().lower()
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


def _load_entity_file(dir_path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    file_rel = str(entry.get("file") or "").strip()
    if not file_rel:
        eid = str(entry.get("id") or "").strip()
        file_rel = f"{eid}.yaml" if eid else ""
    if not file_rel:
        return {}
    p = (dir_path / file_rel).resolve()
    if not p.is_file():
        return {}
    return _load_yaml_dict(p)


def _get_qparam_str(key: str) -> str:
    raw = st.query_params.get(key)
    s = raw[0] if isinstance(raw, list) and raw else (raw or "")
    return str(s or "").strip()


def _set_qparam(key: str, value: str) -> None:
    st.query_params[key] = str(value)
    st.rerun()


def _render_index_tiles(
    *,
    index: dict[str, Any],
    index_key: str,
    label: str,
    section_name: str,
    qparam_key: str,
    icon_prefix: str | None = None,
    cols: int = 4,
    page_size: int = 40,
) -> dict[str, Any] | None:
    entries = index.get(index_key)
    if not isinstance(entries, list):
        st.error(f"Invalid index format: `{index_key}` is not a list.")
        return None

    q = st.text_input("Search", value="", key=f"wiki_db_search_tiles_{index_key}").strip().lower()
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
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=max_page,
        value=min(1 if not selected_id else 1, max_page),
        step=1,
        key=f"wiki_db_tiles_page_{index_key}",
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
            if st.button(btn_label, use_container_width=True, key=f"tile_{index_key}_{eid}_{start+i}"):
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
            _run_script(title="Sync items", script_rel="cmd/sync_items_wiki.py", progress_total_hint=405)
        if st.button("Download images (all)", use_container_width=True):
            _run_script(
                title="Download wiki images (all)",
                script_rel="cmd/download_wiki_images.py",
                args=["all"],
            )

    st.markdown("**Why are some building requirements empty?**")
    st.write(
        "Some building pages do not have a `Requirements` table on the wiki, so we cannot "
        "extract level/cost/time data from them."
    )

    st.markdown("**What is `item_icon_XXX` in build costs?**")
    st.write(
        "The wiki often represents resource types as icons in tables. We store the icon filename "
        "as a stable identifier (e.g. `item_icon_103`) until we add a mapping to canonical resource names."
    )

    st.markdown("**Local assets**")
    st.write("Downloaded images are stored under `db/assets/wiki/`.")


st.title("DB · Wiki reference")
st.caption("Buildings, heroes, items and FAQ — sourced from whiteoutsurvival.wiki.")

repo = _repo_root()

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


def _set_section(section: str) -> None:
    st.query_params["section"] = str(section)
    st.rerun()


section = _get_section()

if section == "home":
    st.markdown("### Reference")
    st.caption("Pick a section.")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Buildings", use_container_width=True):
            _set_section("buildings")
    with c2:
        if st.button("Heroes", use_container_width=True):
            _set_section("heroes")
    with c3:
        if st.button("Items", use_container_width=True):
            _set_section("items")
    with c4:
        if st.button("FAQ", use_container_width=True):
            _set_section("faq")

    st.divider()

tab_buildings, tab_heroes, tab_items, tab_faq = st.tabs(["Buildings", "Heroes", "Items", "FAQ"])

with tab_buildings:
    if section not in {"home", "buildings"}:
        st.caption("Open this tab from the tiles above, or switch `?section=buildings`.")
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

    sel = _render_index_tiles(
        index=idx,
        index_key="buildings",
        label="Buildings",
        section_name="buildings",
        qparam_key="building",
        icon_prefix="buildings",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click a building tile to open its card.")
        sel = _select_from_index(idx, "buildings", "Building")
    if sel:
        building = _load_entity_file(repo / "db" / "buildings", sel)
        _render_building(building)

with tab_heroes:
    if section not in {"home", "heroes"}:
        st.caption("Open this tab from the tiles above, or switch `?section=heroes`.")
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

    sel = _render_index_tiles(
        index=idx,
        index_key="heroes",
        label="Heroes",
        section_name="heroes",
        qparam_key="hero",
        icon_prefix="heroes",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click a hero tile to open its card.")
        sel = _select_from_index(idx, "heroes", "Hero")
    if sel:
        hero = _load_entity_file(repo / "db" / "heroes", sel)
        _render_hero(hero)

with tab_items:
    if section not in {"home", "items"}:
        st.caption("Open this tab from the tiles above, or switch `?section=items`.")
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

    sel = _render_index_tiles(
        index=idx,
        index_key="items",
        label="Items",
        section_name="items",
        qparam_key="item",
        icon_prefix="items",
        cols=int(cols),
        page_size=int(page_size),
    )
    st.divider()
    if not sel:
        st.caption("Tip: click an item tile to open its card.")
        sel = _select_from_index(idx, "items", "Item")
    if sel:
        item = _load_entity_file(repo / "db" / "items", sel)
        _render_item(item)

with tab_faq:
    if section not in {"home", "faq"}:
        st.caption("Open this tab from the tiles above, or switch `?section=faq`.")
    _render_faq()

