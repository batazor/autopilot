"""Gallery nested-table rows and detail preview panel."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
from streamlit_nested_table import table_column

from ui.area_annotator import _format_screen_id_choice, screen_id_select_options
from ui.labeling_gallery_query import open_in_labeling_query_params
from ui.preview_display import png_bytes_fitted

if TYPE_CHECKING:
    from collections.abc import Mapping


def gallery_page_url(page: str, query: dict[str, str]) -> str:
    """Full URL to a Streamlit page with query string."""

    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts:
        parts[-1] = page
        new_path = "/" + "/".join(parts)
    else:
        new_path = "/" + page
    return urlunparse((u.scheme, u.netloc, new_path, "", urlencode(query), ""))


def gallery_table_columns() -> list:
    return [
        table_column("path", "File", width=220),
        table_column("screen_id", "Screen ID", width=140),
        table_column("regions", "Regions", width=80, align="right"),
        table_column("modified", "Modified", width=150),
        table_column("label", "Label", width=72, cell_type="link", link_text_key="label_text"),
    ]


def _row_key(rel: str) -> str:
    return rel.replace("/", "::")


def _rel_under_references(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


def build_gallery_file_row(
    p: Path,
    *,
    ref_root: Path,
    area_mtime: float,
    area_path_str: str,
    area_doc: dict[str, Any],
    module_key: str,
    references_prefix: str,
    gallery_slice_cached: Any,
    display_ref_for_card: Any,
    screen_entry_for_ref: Any,
) -> dict[str, Any]:
    rel = _rel_under_references(p, ref_root)
    row_key = _row_key(rel)
    layout_key = f"layout::{row_key}"
    entry = screen_entry_for_ref(area_doc, rel, references_prefix=references_prefix)
    regs_f, _, sid = gallery_slice_cached(
        area_mtime, area_path_str, rel, "auto", references_prefix
    )
    card_ver = str(st.session_state.get(layout_key, "auto"))
    labeling_ref = display_ref_for_card(
        area_doc, rel, "default", references_prefix=references_prefix
    )
    labeling_qp = open_in_labeling_query_params(
        labeling_ref, card_ver, module_key=module_key
    )
    try:
        ts_raw = p.stat().st_mtime
        modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts_raw))
    except OSError:
        modified = "—"
    sid_show = (sid or "").strip() or "—"
    if not entry and sid_show == "—":
        sid_show = "⚠ unassigned"
    return {
        "id": rel,
        "path": rel,
        "screen_id": sid_show,
        "regions": len(regs_f),
        "modified": modified,
        "label": gallery_page_url("labeling", labeling_qp),
        "label_text": "Open",
        "selectable": True,
    }


def build_gallery_nested_rows(
    paths: list[Path],
    *,
    group_by_page: bool,
    ref_root: Path,
    area_mtime: float,
    area_path_str: str,
    area_doc: dict[str, Any],
    module_key: str,
    references_prefix: str,
    gallery_slice_cached: Any,
    display_ref_for_card: Any,
    screen_entry_for_ref: Any,
) -> list[dict[str, Any]]:
    file_rows = [
        build_gallery_file_row(
            p,
            ref_root=ref_root,
            area_mtime=area_mtime,
            area_path_str=area_path_str,
            area_doc=area_doc,
            module_key=module_key,
            references_prefix=references_prefix,
            gallery_slice_cached=gallery_slice_cached,
            display_ref_for_card=display_ref_for_card,
            screen_entry_for_ref=screen_entry_for_ref,
        )
        for p in paths
    ]
    if not group_by_page:
        return sorted(file_rows, key=lambda r: str(r.get("path") or ""))

    by_node: dict[str, list[dict[str, Any]]] = {}
    for row in file_rows:
        sid = str(row.get("screen_id") or "—").strip()
        if sid.startswith("⚠"):
            sid = "(unassigned)"
        by_node.setdefault(sid, []).append(row)

    out: list[dict[str, Any]] = []
    for sid in sorted(by_node.keys(), key=lambda s: (s == "(unassigned)", s.lower())):
        children = sorted(by_node[sid], key=lambda r: str(r.get("path") or ""))
        reg_total = sum(int(c.get("regions") or 0) for c in children)
        out.append(
            {
                "id": f"gallery-group:{sid}",
                "path": f"{sid}/",
                "screen_id": sid,
                "regions": reg_total,
                "modified": "",
                "label": "",
                "label_text": "",
                "selectable": False,
                "subRows": children,
            }
        )
    return out


def sync_gallery_selection(
    selection: Mapping[str, Any] | None,
    *,
    filtered_rels: set[str],
) -> str | None:
    """Update ``gallery_selected_rel`` from nested-table click; return active rel."""
    st.session_state.setdefault("gallery_selected_rel", "")
    if isinstance(selection, dict):
        row = selection.get("row")
        if isinstance(row, dict) and row.get("selectable") is not False:
            rid = str(selection.get("rowId") or row.get("id") or "").strip()
            if rid and rid in filtered_rels:
                st.session_state["gallery_selected_rel"] = rid
    rel = str(st.session_state.get("gallery_selected_rel") or "").strip()
    if rel and rel not in filtered_rels:
        st.session_state.pop("gallery_selected_rel", None)
        rel = ""
    return rel or None


def render_gallery_detail(
    rel: str,
    *,
    ref_root: Path,
    area_path: Path,
    area_mtime: float,
    area_path_str: str,
    area_doc: dict[str, Any],
    module_key: str,
    references_prefix: str,
    thumb_max: int,
    gallery_slice_cached: Any,
    display_ref_for_card: Any,
    screen_entry_for_ref: Any,
    layout_ver_labels_for_entry: Any,
    set_screen_id_for_ref: Any,
    annotate_regions_png: Any,
    clear_area_caches: Any,
) -> None:
    p = ref_root / rel
    if not p.is_file():
        st.warning(f"File not found: `references/{rel}`")
        return

    row_key = _row_key(rel)
    layout_key = f"layout::{row_key}"
    entry = screen_entry_for_ref(area_doc, rel, references_prefix=references_prefix)
    ver_labels = layout_ver_labels_for_entry(entry, references_prefix=references_prefix)
    ver_keys = list(ver_labels.keys())
    show_layout_switch = len(ver_keys) > 1

    st.markdown(f"**{Path(rel).name}**")
    st.caption(f"`{rel}`")

    if show_layout_switch:
        _layout_default_idx = ver_keys.index("default") if "default" in ver_keys else 0
        cur_layout = st.session_state.get(layout_key)
        if cur_layout not in ver_keys:
            st.session_state[layout_key] = ver_keys[_layout_default_idx]
        card_ver = st.selectbox(
            "Layout",
            options=ver_keys,
            format_func=lambda k, labels=ver_labels: labels[k],
            key=layout_key,
            help="**Auto**: gallery file + worker-style regions. "
            "**Default** / **Force vN**: switch reference image and regions.",
        )
    else:
        card_ver = "auto"
        st.session_state[layout_key] = "auto"

    highlight = st.toggle(
        "Highlight regions",
        value=False,
        key=f"hl::{row_key}",
    )
    labeling_ref = display_ref_for_card(
        area_doc, rel, "default", references_prefix=references_prefix
    )
    st.page_link(
        "views/labeling.py",
        label="Open in Labeling",
        query_params=open_in_labeling_query_params(
            labeling_ref, card_ver, module_key=module_key
        ),
        width="stretch",
    )

    layout_rel = display_ref_for_card(
        area_doc, rel, card_ver, references_prefix=references_prefix
    )
    img_path = (ref_root / layout_rel).resolve()
    try:
        if img_path.is_file():
            data = img_path.read_bytes()
            ts_raw = img_path.stat().st_mtime
        else:
            st.caption(f"No file `{layout_rel}` — showing gallery row `{rel}`.")
            data = p.read_bytes()
            ts_raw = p.stat().st_mtime
        native_png = data
    except OSError as exc:
        st.error(f"`{rel}` / `{layout_rel}`: {exc}")
        return

    regs_f, bbox_regs, sid = gallery_slice_cached(
        area_mtime, area_path_str, rel, card_ver, references_prefix
    )
    extra: list[str] = []
    if (sid or "").strip():
        extra.append(f"page={sid.strip()}")
    if regs_f:
        extra.append(f"regions={len(regs_f)}")
    extra_txt = (" · " + " · ".join(extra)) if extra else ""
    cap_rel = f"`{layout_rel}`"
    if rel != layout_rel:
        cap_rel = f"{cap_rel} · row `{rel}`"

    try:
        if highlight and bbox_regs:
            annotated = annotate_regions_png(native_png, bbox_regs)
            fitted, native, _disp = png_bytes_fitted(annotated, thumb_max)
        else:
            fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)
    except Exception as exc:
        st.error(f"`{layout_rel}`: load failed: {exc}")
        fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)

    if highlight and not bbox_regs:
        st.caption("No region boxes in `area.json` for this layout.")
    st.image(
        fitted,
        caption=f"{cap_rel} · {native[0]}×{native[1]}{extra_txt}",
        width="stretch",
    )
    st.caption(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_raw)))
    try:
        _sid_opts = screen_id_select_options(area_doc, sid or "")  # ty: ignore[invalid-argument-type]
    except Exception:
        _sid_opts = [""]
    _cur = (sid or "").strip()
    _sid_idx = _sid_opts.index(_cur) if _cur in _sid_opts else 0
    _selected_sid = st.selectbox(
        "Screen ID (node)",
        options=_sid_opts,
        index=_sid_idx,
        format_func=_format_screen_id_choice,
        key=f"node::{row_key}",
        help="Logical game node for this reference (saved to area.json).",
    )
    if (_selected_sid or "") != (sid or ""):
        ok, err = set_screen_id_for_ref(
            rel,
            _selected_sid or "",
            area_path=area_path,
            references_prefix=references_prefix,
        )
        if ok:
            st.toast(
                f"`{rel}` → node = " + (_selected_sid or "(none)"),
                icon="✅",
            )
            clear_area_caches()
            st.rerun()
        else:
            st.error(f"Save failed: {err}")
