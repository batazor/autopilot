"""Gallery: browse `references/*.png` with filters and grouping."""
from __future__ import annotations

from collections import Counter, OrderedDict
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw
from streamlit_nested_table import nested_table

from config.module_registry import collect_reference_rels_from_doc
from layout.area_regions import is_auxiliary_overlay_region
from layout.area_versions import (
    effective_ocr_for_region,
    get_version_block,
    normalize_version_id,
    resolve_region_with_version,
)
from ui.area_annotator import REPO_ROOT, load_json
from ui.gallery_table import (
    build_gallery_nested_rows,
    gallery_table_columns,
    render_gallery_detail,
    sync_gallery_selection,
)
from ui.reference_preview import list_reference_pngs
from ui.wiki_module import render_wiki_module_selector


def _set_screen_id_for_ref(
    ref_rel: str,
    new_sid: str,
    *,
    area_path: Path,
    references_prefix: str,
) -> tuple[bool, str]:
    """Update ``screen_id`` of the area manifest screen entry matching ``ref_rel``.

    Returns ``(ok, message)``. The entry is the one whose ``ocr`` (or any
    ``versions[].ocr``) resolves to ``ref_rel`` — same lookup used by
    ``_screen_entry_for_ref``. ``new_sid`` may be empty to unassign.
    """
    if not area_path.is_file():
        return False, f"{area_path.name} not found"
    try:
        doc = load_json(area_path)
    except Exception as exc:
        return False, f"parse failed: {exc}"
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return False, "area.json has no `screens` list"
    target = None
    for e in screens:
        if not isinstance(e, dict):
            continue
        if any(
            r == ref_rel
            for r, _ in _refs_from_screen_entry(e, references_prefix=references_prefix)
        ):
            target = e
            break
    if target is None:
        return False, f"no screen entry references `{ref_rel}`"
    target["screen_id"] = str(new_sid or "").strip()
    try:
        from ui.area_annotator import save_json

        save_json(area_path, doc)
    except OSError as exc:
        return False, f"write failed: {exc}"
    return True, ""


def _rel_under_references(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


def _node_group_for(rel: str, sid: str | None) -> str:
    """Pick the gallery group for a reference file.

    Non-empty ``screen_id`` wins. Otherwise fall back to the top-level
    directory under ``references/`` (e.g. ``events``) so on-disk layout
    keeps icons together even before they're mapped in ``area.json``.
    Files directly under ``references/`` stay in ``(unassigned)``.
    """
    s = (sid or "").strip()
    if s:
        return s
    head = (rel or "").replace("\\", "/").split("/", 1)
    if len(head) == 2 and head[0]:
        return head[0]
    return "(unassigned)"


@st.cache_data(ttl=60)
def _load_area_doc_cached(mtime: float, area_path_str: str) -> dict[str, object]:
    """Cache parsed area manifest keyed by mtime (fast reruns)."""
    if mtime <= 0:
        return {}
    area_path = Path(area_path_str)
    if not area_path.is_file():
        return {}
    try:
        return load_json(area_path)
    except Exception:
        return {}


def _ocr_to_ref_rel(ocr: str, references_prefix: str | None = None) -> str | None:
    path = str(ocr or "").replace("\\", "/").strip()
    if not path:
        return None
    if references_prefix:
        prefix = references_prefix.strip().rstrip("/")
        if path == prefix or path.startswith(f"{prefix}/"):
            return path[len(prefix) :].lstrip("/")
    try:
        return Path(path).relative_to("references").as_posix()
    except Exception:
        name = Path(path).name
        return name if name else None


def _declared_version_ids(entry: dict[str, object]) -> set[str]:
    out: set[str] = set()
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return out
    for v in raw:
        if not isinstance(v, dict):
            continue
        vid = normalize_version_id(str(v.get("id") or ""))
        if vid:
            out.add(vid)
    return out


def _refs_from_screen_entry(
    entry: dict[str, object],
    *,
    references_prefix: str = "references",
) -> list[tuple[str, str | None]]:
    """``(ref_rel, auto_active_version)`` — ``None`` means default (v1) screenshot."""
    out: list[tuple[str, str | None]] = []
    rel = _ocr_to_ref_rel(str(entry.get("ocr") or ""), references_prefix)
    if rel:
        out.append((rel, None))
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return out
    for v in raw:
        if not isinstance(v, dict):
            continue
        vid = normalize_version_id(str(v.get("id") or ""))
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""), references_prefix)
        if vid and vr:
            out.append((vr, vid))
    return out


def _active_version_for_ref(
    ref_rel: str,
    entry: dict[str, object],
    preview_mode: str,
    *,
    references_prefix: str = "references",
) -> str | None:
    if preview_mode == "auto":
        for r, av in _refs_from_screen_entry(entry, references_prefix=references_prefix):
            if r == ref_rel:
                return av
        return None
    if preview_mode == "default":
        return None
    return preview_mode if preview_mode.startswith("v") else None


def _merged_bbox_entries_for_ref(
    entry: dict[str, object],
    ref_rel: str,
    preview_mode: str,
    *,
    references_prefix: str = "references",
) -> list[dict[str, object]]:
    """Collect bbox rows for ``ref_rel`` under ``preview_mode``.

    For **auto** / **default**, keep worker parity: only regions whose
    ``effective_ocr_for_region`` matches this screenshot path.

    For **forced ``vN``**, skip that filter so version-only regions (whose crop
    lives on ``versions[V].ocr``) still appear when viewing the default
    reference image — switching to “Force v2” on ``main_city.png`` should not
    hide v2-exclusive boxes.
    """
    av = _active_version_for_ref(
        ref_rel, entry, preview_mode, references_prefix=references_prefix
    )
    skip_eff_match = preview_mode not in ("auto", "default")

    candidate_names: set[str] = set()
    for r in entry.get("regions") or []:
        if isinstance(r, dict):
            nm = str(r.get("name") or "").strip()
            if nm:
                candidate_names.add(nm)
    if av:
        ver_block = get_version_block(entry, av)
        if ver_block is not None:
            for r in ver_block.get("regions") or []:
                if isinstance(r, dict):
                    nm = str(r.get("name") or "").strip()
                    if nm:
                        candidate_names.add(nm)

    by_name: dict[str, dict[str, object]] = {}
    for name in candidate_names:
        resolved = resolve_region_with_version(entry, name, av)
        if resolved is None or not isinstance(resolved, dict):
            continue
        if not skip_eff_match:
            eff = effective_ocr_for_region(entry, resolved)
            eff_rel = _ocr_to_ref_rel(eff, references_prefix)
            if eff_rel != ref_rel:
                continue
        nm = str(resolved.get("name") or "").strip()
        bb = resolved.get("bbox")
        if nm and isinstance(bb, dict):
            by_name[nm] = {"name": nm, "bbox": bb}
    return list(by_name.values())


def _screen_entry_for_ref(
    doc: dict[str, object],
    ref_rel: str,
    *,
    references_prefix: str = "references",
) -> dict[str, object] | None:
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return None
    for e in screens:
        if not isinstance(e, dict):
            continue
        ref_pairs = _refs_from_screen_entry(e, references_prefix=references_prefix)
        if any(r == ref_rel for r, _ in ref_pairs):
            return e
    return None


def _screen_has_layout_versions(
    entry: dict[str, object] | None,
    *,
    references_prefix: str = "references",
) -> bool:
    """True when the screen declares at least one ``versions[]`` entry with id + ``ocr`` path."""
    if entry is None:
        return False
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return False
    for v in raw:
        if not isinstance(v, dict):
            continue
        vid = normalize_version_id(str(v.get("id") or ""))
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""), references_prefix)
        if vid and vr:
            return True
    return False


def _layout_ver_labels_for_entry(
    entry: dict[str, object] | None,
    *,
    references_prefix: str = "references",
) -> OrderedDict[str, str]:
    """Options: **Auto** only without ``versions``; else Auto, Default, Force vN per entry."""
    od: OrderedDict[str, str] = OrderedDict()
    od["auto"] = "Auto (match file)"
    if entry is None or not _screen_has_layout_versions(
        entry, references_prefix=references_prefix
    ):
        return od
    od["default"] = "Default (v1)"
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return od
    seen: set[str] = set()
    for v in raw:
        if not isinstance(v, dict):
            continue
        vid = normalize_version_id(str(v.get("id") or ""))
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""), references_prefix)
        if not vid or not vr or vid in seen:
            continue
        seen.add(vid)
        od[vid] = f"Force {vid}"
    return od


def _display_ref_rel_for_card(
    doc: dict[str, object],
    listed_rel: str,
    preview_mode: str,
    *,
    references_prefix: str = "references",
) -> str:
    """Which ``references/…`` image to show for this gallery row and layout mode."""
    entry = _screen_entry_for_ref(doc, listed_rel, references_prefix=references_prefix)
    if entry is None:
        return listed_rel
    if preview_mode == "auto":
        return listed_rel
    if preview_mode == "default":
        dr = _ocr_to_ref_rel(str(entry.get("ocr") or ""), references_prefix)
        return dr if dr else listed_rel
    vid = normalize_version_id(preview_mode)
    if not vid:
        return listed_rel
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return listed_rel
    for v in raw:
        if not isinstance(v, dict):
            continue
        if normalize_version_id(str(v.get("id") or "")) != vid:
            continue
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""), references_prefix)
        if vr:
            return vr
    return listed_rel


def _gallery_slice_for_ref(
    doc: dict[str, object],
    listed_rel: str,
    preview_mode: str,
    *,
    references_prefix: str = "references",
) -> tuple[set[str], list[dict[str, object]], str]:
    """Resolve regions against the **displayed** reference (layout-matched screenshot)."""
    layout_ref = _display_ref_rel_for_card(
        doc, listed_rel, preview_mode, references_prefix=references_prefix
    )
    regions: set[str] = set()
    boxes_by_name: dict[str, dict[str, object]] = {}
    sid_out = ""
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return set(), [], ""
    for e in screens:
        if not isinstance(e, dict):
            continue
        ref_pairs = _refs_from_screen_entry(e, references_prefix=references_prefix)
        if not any(r == listed_rel for r, _ in ref_pairs):
            continue
        sid = str(e.get("screen_id") or "").strip()
        if sid:
            sid_out = sid
        for b in _merged_bbox_entries_for_ref(
            e, layout_ref, preview_mode, references_prefix=references_prefix
        ):
            nm = str(b.get("name") or "").strip()
            if nm:
                regions.add(nm)
                boxes_by_name[nm] = b
    return regions, list(boxes_by_name.values()), sid_out


@st.cache_data(ttl=60)
def _gallery_slice_cached(
    mtime: float,
    area_path_str: str,
    ref_rel: str,
    preview_mode: str,
    references_prefix: str,
) -> tuple[frozenset[str], list[dict[str, object]], str]:
    doc = _load_area_doc_cached(mtime, area_path_str)
    regs, boxes, sid = _gallery_slice_for_ref(
        doc, ref_rel, preview_mode, references_prefix=references_prefix
    )
    return frozenset(regs), boxes, sid


def _refs_exclusive_to_versions(doc: dict[str, object]) -> frozenset[str]:
    """Reference paths that appear only under ``versions[].ocr``, never as screen ``ocr``.

    Those files are not listed as separate gallery rows — use **Layout → Force vN**
    on the base screenshot row.
    """
    default_refs: set[str] = set()
    version_refs: set[str] = set()
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return frozenset()
    for e in screens:
        if not isinstance(e, dict):
            continue
        dr = _ocr_to_ref_rel(str(e.get("ocr") or ""))
        if dr:
            default_refs.add(dr)
        raw = e.get("versions")
        if not isinstance(raw, list):
            continue
        for v in raw:
            if not isinstance(v, dict):
                continue
            vr = _ocr_to_ref_rel(str(v.get("ocr") or ""))
            if vr:
                version_refs.add(vr)
    return frozenset(version_refs - default_refs)


@st.cache_data(ttl=60)
def _refs_exclusive_to_versions_cached(mtime: float, area_path_str: str) -> frozenset[str]:
    doc = _load_area_doc_cached(mtime, area_path_str)
    return _refs_exclusive_to_versions(doc)


def _primary_region_names_for_filter(doc: dict[str, object]) -> list[str]:
    """Region names for Gallery filter: exclude overlay search ROIs and tap helpers.

    Walks base ``regions[]`` and every ``versions[].regions[]`` so version-only
    names (e.g. a button that only exists in v2) appear in the filter.
    """
    names: set[str] = set()
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return []
    for e in screens:
        if not isinstance(e, dict):
            continue
        for source in (e.get("regions"), *(
            v.get("regions") for v in (e.get("versions") or []) if isinstance(v, dict)
        )):
            if not isinstance(source, list):
                continue
            for r in source:
                if not isinstance(r, dict):
                    continue
                if is_auxiliary_overlay_region(r):
                    continue
                nm = str(r.get("name") or "").strip()
                if nm:
                    names.add(nm)
    return sorted(names)


def _bbox_pct_to_px(bbox: dict[str, object], *, w: int, h: int) -> tuple[int, int, int, int] | None:
    try:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        bw = float(bbox.get("width", 0.0))
        bh = float(bbox.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    x1 = int(round((x / 100.0) * w))
    y1 = int(round((y / 100.0) * h))
    x2 = int(round(((x + bw) / 100.0) * w))
    y2 = int(round(((y + bh) / 100.0) * h))
    x1 = max(0, min(w - 1, x1))
    y1 = max(0, min(h - 1, y1))
    x2 = max(0, min(w, x2))
    y2 = max(0, min(h, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _annotate_regions_png(png: bytes, regions: list[dict[str, object]]) -> bytes:
    """Red outlines from ``area.json`` bboxes (native resolution, same downscale as thumb)."""
    im = Image.open(BytesIO(png)).convert("RGBA")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    for e in regions:
        if not isinstance(e, dict):
            continue
        bb = e.get("bbox")
        if not isinstance(bb, dict):
            continue
        rect = _bbox_pct_to_px(bb, w=w, h=h)
        if rect is None:
            continue
        x1, y1, x2, y2 = rect
        draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0, 255), width=2)
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


st.title("Gallery")

wiki_ctx = render_wiki_module_selector(
    help="Browse and annotate references for Core or a feature module.",
)
ref_root = wiki_ctx.references_dir
_area_path = wiki_ctx.area_path
_ref_prefix = wiki_ctx.references_prefix
try:
    area_mtime = _area_path.stat().st_mtime if _area_path.is_file() else 0.0
except OSError:
    area_mtime = 0.0
area_doc = _load_area_doc_cached(area_mtime, str(_area_path))
_module_refs: set[str] | None = None
if wiki_ctx.module_id:
    declared = collect_reference_rels_from_doc(area_doc, wiki_ctx)
    if declared:
        _module_refs = declared
all_regions = _primary_region_names_for_filter(area_doc)

def _qp_get_str(key: str, default: str = "") -> str:
    v = st.query_params.get(key)
    if v is None:
        return default
    if isinstance(v, list):
        return str(v[0]) if v else default
    return str(v)


def _qp_get_int(key: str, default: int) -> int:
    s = _qp_get_str(key, "")
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _qp_get_bool(key: str, default: bool = False) -> bool:
    s = _qp_get_str(key, "")
    if not s:
        return default
    return s.strip().lower() in {"1", "true", "yes", "on"}


def _qp_get_regions(key: str) -> list[str]:
    raw = st.query_params.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            parts.extend(str(item).split(","))
    else:
        parts = str(raw).split(",")
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        s = str(p).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


qp_limit = _qp_get_int("limit", 2000)
qp_group = _qp_get_str("group", "page")
qp_q = _qp_get_str("q", "")
qp_temporal = _qp_get_bool("temporal", False)
qp_crop = _qp_get_bool("crop", False)
qp_regions = [r for r in _qp_get_regions("regions") if r in set(all_regions)]

top = st.columns([1.1, 1.0, 2.2], vertical_alignment="bottom")
with top[0]:
    limit = int(
        st.number_input(
            "Max files (newest-first)",
            min_value=50,
            max_value=20000,
            value=max(50, min(20000, qp_limit)),
            step=50,
            help="Loads up to this many PNGs from disk (newest-first).",
        )
    )
with top[1]:
    thumb_max = 500
    group_by = st.selectbox(
        "Group by",
        options=["none", "page (screen_id)"],
        index=(0 if qp_group == "none" else 1),
    )
with top[2]:
    q = st.text_input(
        "Filter by filename",
        value=qp_q,
        placeholder="e.g. main_city, main_city.to.new_people, rookie…",
    )

flt = st.columns([1, 1], vertical_alignment="bottom")
with flt[0]:
    show_temporal = st.toggle("Include `references/temporal/`", value=qp_temporal)
with flt[1]:
    show_crop = st.toggle("Include `references/crop/`", value=qp_crop)

region_sel = st.multiselect(
    "Filter by regions (from `area.json`)",
    options=all_regions,
    default=qp_regions,
    help="Primary regions only (no `*_search` zones, `*_tap` points, or `overlay_auxiliary`). "
    "Show screenshots that contain **any** selected region.",
)

def _sync_query_params() -> None:
    qp: dict[str, object] = {"module": wiki_ctx.query_value}
    ql = q.strip()
    if ql:
        qp["q"] = ql
    if limit != 2000:
        qp["limit"] = str(int(limit))
    grp_key = "none" if group_by == "none" else "page"
    if grp_key != "page":
        qp["group"] = grp_key
    if show_temporal:
        qp["temporal"] = "1"
    if show_crop:
        qp["crop"] = "1"
    regs = [str(x).strip() for x in (region_sel or []) if str(x).strip()]
    if regs:
        qp["regions"] = ",".join(regs)
    # Avoid needless reruns when already in sync.
    if dict(st.query_params) != qp:
        st.query_params.clear()
        st.query_params.update(qp)


_sync_query_params()

files = list_reference_pngs(
    limit=limit,
    root=ref_root,
    exclude_temporal=not show_temporal,
    exclude_crop=not show_crop,
)

_version_only_refs = _refs_exclusive_to_versions_cached(area_mtime, str(_area_path))
_node_counts: Counter[str] = Counter()
for _p in files:
    _rrel = _rel_under_references(_p, ref_root)
    if _rrel in _version_only_refs:
        continue
    _, _, _sid = _gallery_slice_cached(
        area_mtime, str(_area_path), _rrel, "auto", _ref_prefix
    )
    _nk = _node_group_for(_rrel, _sid)
    _node_counts[_nk] += 1
_node_pill_labels = [
    f"{name} · {cnt}"
    for name, cnt in sorted(
        _node_counts.items(),
        key=lambda kv: (kv[0] == "(unassigned)", kv[0].lower()),
    )
]
_node_label_to_id: dict[str, str] = {
    f"{name} · {cnt}": name for name, cnt in _node_counts.items()
}

with st.sidebar:
    st.caption("Screen ID (node)")
    _node_pick = st.pills(
        "Gallery nodes",
        options=_node_pill_labels,
        selection_mode="multi",
        default=[],
        label_visibility="collapsed",
        key="gallery_node_pills",
        help="Limit rows to PNGs tied to these ``area.json`` ``screen_id`` values (Auto layout). "
        "Empty = all nodes.",
    )
_selected_nodes: set[str] = {
    _node_label_to_id[lab] for lab in (_node_pick or []) if lab in _node_label_to_id
}

ql = q.strip().lower()
want_regions = {str(x).strip() for x in (region_sel or []) if str(x).strip()}
filtered: list[Path] = []
for p in files:
    rel = _rel_under_references(p, ref_root)
    if rel in _version_only_refs:
        continue
    if _module_refs is not None and rel not in _module_refs:
        continue
    have_f, _, sid_line = _gallery_slice_cached(
        area_mtime, str(_area_path), rel, "auto", _ref_prefix
    )
    node_key = _node_group_for(rel, sid_line)
    if _selected_nodes and node_key not in _selected_nodes:
        continue
    if ql and ql not in rel.lower():
        continue
    if want_regions and not (want_regions & set(have_f)):
        continue
    filtered.append(p)

st.caption(
    f"Module **{wiki_ctx.title}** · showing **{len(filtered)} / {len(files)}** file(s) · "
    f"root: `{ref_root}` · area: `{_area_path.relative_to(REPO_ROOT)}` · "
    "Preview left · table right · version-only PNGs hidden (use **Layout → Force vN** on the base row)."
)

if not filtered:
    st.info("No PNGs found under `references/` (excluding `temporal/` and `crop/`).")
    st.stop()

_filtered_rels = {_rel_under_references(p, ref_root) for p in filtered}
_nested_rows = build_gallery_nested_rows(
    filtered,
    group_by_page=(group_by == "page (screen_id)"),
    ref_root=ref_root,
    area_mtime=area_mtime,
    area_path_str=str(_area_path),
    area_doc=area_doc,
    module_key=wiki_ctx.query_value,
    references_prefix=_ref_prefix,
    gallery_slice_cached=_gallery_slice_cached,
    display_ref_for_card=_display_ref_rel_for_card,
    screen_entry_for_ref=_screen_entry_for_ref,
)

if not st.session_state.get("gallery_selected_rel") and filtered:
    st.session_state["gallery_selected_rel"] = _rel_under_references(filtered[0], ref_root)

_preview_col, _table_col = st.columns([1, 1.55], gap="medium")

_table_selection: dict | None = None
# Table column runs first so row-click updates selection before the preview renders.
with _table_col:
    _table_selection = nested_table(
        _nested_rows,
        gallery_table_columns(),
        sub_rows_key="subRows",
        height=640,
        default_expanded=False,
        striped=True,
        selectable=True,
        key="gallery_nested_table",
    )
_sel_rel = sync_gallery_selection(
    _table_selection if isinstance(_table_selection, dict) else None,
    filtered_rels=_filtered_rels,
)

with _preview_col:
    if _sel_rel:
        render_gallery_detail(
            _sel_rel,
            ref_root=ref_root,
            area_path=_area_path,
            area_mtime=area_mtime,
            area_path_str=str(_area_path),
            area_doc=area_doc,
            module_key=wiki_ctx.query_value,
            references_prefix=_ref_prefix,
            thumb_max=thumb_max,
            gallery_slice_cached=_gallery_slice_cached,
            display_ref_for_card=_display_ref_rel_for_card,
            screen_entry_for_ref=_screen_entry_for_ref,
            layout_ver_labels_for_entry=_layout_ver_labels_for_entry,
            set_screen_id_for_ref=_set_screen_id_for_ref,
            annotate_regions_png=_annotate_regions_png,
            clear_area_caches=lambda: (
                _load_area_doc_cached.clear(),
                _gallery_slice_cached.clear(),
            ),
        )
    else:
        st.info("Select a row in the table →")

