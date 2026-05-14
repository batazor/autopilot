"""Gallery: browse `references/*.png` with filters and grouping."""

from __future__ import annotations

import json
import time
from collections import Counter, OrderedDict
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw

from layout.area_regions import is_auxiliary_overlay_region
from layout.area_versions import (
    effective_ocr_for_region,
    get_version_block,
    normalize_version_id,
    resolve_region_with_version,
)
from ui.area_annotator import _format_screen_id_choice, screen_id_select_options
from ui.labeling_gallery_query import open_in_labeling_query_params
from ui.preview_display import png_bytes_fitted
from ui.reference_preview import list_reference_pngs, references_root

_AREA_JSON_PATH = Path(__file__).resolve().parents[2] / "area.json"




def _set_screen_id_for_ref(ref_rel: str, new_sid: str) -> tuple[bool, str]:
    """Update ``screen_id`` of the area.json screen entry matching ``ref_rel``.

    Returns ``(ok, message)``. The entry is the one whose ``ocr`` (or any
    ``versions[].ocr``) resolves to ``ref_rel`` — same lookup used by
    ``_screen_entry_for_ref``. ``new_sid`` may be empty to unassign.
    """
    if not _AREA_JSON_PATH.is_file():
        return False, "area.json not found"
    try:
        doc = json.loads(_AREA_JSON_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"parse failed: {exc}"
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return False, "area.json has no `screens` list"
    target = None
    for e in screens:
        if not isinstance(e, dict):
            continue
        if any(r == ref_rel for r, _ in _refs_from_screen_entry(e)):
            target = e
            break
    if target is None:
        return False, f"no screen entry references `{ref_rel}`"
    target["screen_id"] = str(new_sid or "").strip()
    try:
        _AREA_JSON_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
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
def _load_area_doc_cached(mtime: float) -> dict[str, object]:
    """Cache parsed `area.json` keyed by mtime (fast reruns)."""
    if mtime <= 0:
        return {}
    area_path = Path(__file__).resolve().parents[2] / "area.json"
    if not area_path.is_file():
        return {}
    try:
        return json.loads(area_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ocr_to_ref_rel(ocr: str) -> str | None:
    path = str(ocr or "").replace("\\", "/").strip()
    if not path:
        return None
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


def _refs_from_screen_entry(entry: dict[str, object]) -> list[tuple[str, str | None]]:
    """``(ref_rel, auto_active_version)`` — ``None`` means default (v1) screenshot."""
    out: list[tuple[str, str | None]] = []
    rel = _ocr_to_ref_rel(str(entry.get("ocr") or ""))
    if rel:
        out.append((rel, None))
    raw = entry.get("versions")
    if not isinstance(raw, list):
        return out
    for v in raw:
        if not isinstance(v, dict):
            continue
        vid = normalize_version_id(str(v.get("id") or ""))
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""))
        if vid and vr:
            out.append((vr, vid))
    return out


def _active_version_for_ref(
    ref_rel: str,
    entry: dict[str, object],
    preview_mode: str,
) -> str | None:
    if preview_mode == "auto":
        for r, av in _refs_from_screen_entry(entry):
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
) -> list[dict[str, object]]:
    """Collect bbox rows for ``ref_rel`` under ``preview_mode``.

    For **auto** / **default**, keep worker parity: only regions whose
    ``effective_ocr_for_region`` matches this screenshot path.

    For **forced ``vN``**, skip that filter so version-only regions (whose crop
    lives on ``versions[V].ocr``) still appear when viewing the default
    reference image — switching to “Force v2” on ``main_city.png`` should not
    hide v2-exclusive boxes.
    """
    av = _active_version_for_ref(ref_rel, entry, preview_mode)
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
            eff_rel = _ocr_to_ref_rel(eff)
            if eff_rel != ref_rel:
                continue
        nm = str(resolved.get("name") or "").strip()
        bb = resolved.get("bbox")
        if nm and isinstance(bb, dict):
            by_name[nm] = {"name": nm, "bbox": bb}
    return list(by_name.values())


def _screen_entry_for_ref(doc: dict[str, object], ref_rel: str) -> dict[str, object] | None:
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return None
    for e in screens:
        if not isinstance(e, dict):
            continue
        ref_pairs = _refs_from_screen_entry(e)
        if any(r == ref_rel for r, _ in ref_pairs):
            return e
    return None


def _screen_has_layout_versions(entry: dict[str, object] | None) -> bool:
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
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""))
        if vid and vr:
            return True
    return False


def _layout_ver_labels_for_entry(entry: dict[str, object] | None) -> OrderedDict[str, str]:
    """Options: **Auto** only without ``versions``; else Auto, Default, Force vN per entry."""
    od: OrderedDict[str, str] = OrderedDict()
    od["auto"] = "Auto (match file)"
    if entry is None or not _screen_has_layout_versions(entry):
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
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""))
        if not vid or not vr or vid in seen:
            continue
        seen.add(vid)
        od[vid] = f"Force {vid}"
    return od


def _display_ref_rel_for_card(
    doc: dict[str, object],
    listed_rel: str,
    preview_mode: str,
) -> str:
    """Which ``references/…`` image to show for this gallery row and layout mode."""
    entry = _screen_entry_for_ref(doc, listed_rel)
    if entry is None:
        return listed_rel
    if preview_mode == "auto":
        return listed_rel
    if preview_mode == "default":
        dr = _ocr_to_ref_rel(str(entry.get("ocr") or ""))
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
        vr = _ocr_to_ref_rel(str(v.get("ocr") or ""))
        if vr:
            return vr
    return listed_rel


def _gallery_slice_for_ref(
    doc: dict[str, object],
    listed_rel: str,
    preview_mode: str,
) -> tuple[set[str], list[dict[str, object]], str]:
    """Resolve regions against the **displayed** reference (layout-matched screenshot)."""
    layout_ref = _display_ref_rel_for_card(doc, listed_rel, preview_mode)
    regions: set[str] = set()
    boxes_by_name: dict[str, dict[str, object]] = {}
    sid_out = ""
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return set(), [], ""
    for e in screens:
        if not isinstance(e, dict):
            continue
        ref_pairs = _refs_from_screen_entry(e)
        if not any(r == listed_rel for r, _ in ref_pairs):
            continue
        sid = str(e.get("screen_id") or "").strip()
        if sid:
            sid_out = sid
        for b in _merged_bbox_entries_for_ref(e, layout_ref, preview_mode):
            nm = str(b.get("name") or "").strip()
            if nm:
                regions.add(nm)
                boxes_by_name[nm] = b
    return regions, list(boxes_by_name.values()), sid_out


@st.cache_data(ttl=60)
def _gallery_slice_cached(
    mtime: float,
    ref_rel: str,
    preview_mode: str,
) -> tuple[frozenset[str], list[dict[str, object]], str]:
    doc = _load_area_doc_cached(mtime)
    regs, boxes, sid = _gallery_slice_for_ref(doc, ref_rel, preview_mode)
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
def _refs_exclusive_to_versions_cached(mtime: float) -> frozenset[str]:
    doc = _load_area_doc_cached(mtime)
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

ref_root = references_root()
_area_path = Path(__file__).resolve().parents[2] / "area.json"
try:
    area_mtime = _area_path.stat().st_mtime if _area_path.is_file() else 0.0
except OSError:
    area_mtime = 0.0
area_doc = _load_area_doc_cached(area_mtime)
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
        placeholder="e.g. main_city, isNewPeople, rookie…",
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
    qp: dict[str, object] = {}
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
    exclude_temporal=not show_temporal,
    exclude_crop=not show_crop,
)

_version_only_refs = _refs_exclusive_to_versions_cached(area_mtime)
_node_counts: Counter[str] = Counter()
for _p in files:
    _rrel = _rel_under_references(_p, ref_root)
    if _rrel in _version_only_refs:
        continue
    _, _, _sid = _gallery_slice_cached(area_mtime, _rrel, "auto")
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
    have_f, _, sid_line = _gallery_slice_cached(area_mtime, rel, "auto")
    node_key = _node_group_for(rel, sid_line)
    if _selected_nodes and node_key not in _selected_nodes:
        continue
    if ql and ql not in rel.lower():
        continue
    if want_regions and not (want_regions & set(have_f)):
        continue
    filtered.append(p)

st.caption(
    f"Showing **{len(filtered)} / {len(files)}** file(s) · root: `{ref_root}` · "
    "only **base** ``ocr`` screenshots listed — alternates via **Layout** · "
    "**Layout** when ``versions`` exist · defaults to **Default (v1)** · "
    "region filter **Auto** · **Open in Labeling**: default ``ocr``, "
    "``version`` in the link matches **Layout** (incl. ``default`` for Auto)."
)

if not filtered:
    st.info("No PNGs found under `references/` (excluding `temporal/` and `crop/`).")
    st.stop()

def _render_cards(
    paths: list[Path],
    *,
    area_mtime: float,
    area_doc: dict[str, object],
) -> None:
    for p in paths:
        rel = _rel_under_references(p, ref_root)
        # Widget keys are tied to ``rel`` (stable per file), NOT to a
        # positional index or per-group prefix — otherwise editing a row's
        # ``screen_id`` reshuffles group membership and Streamlit reuses
        # widget state by key, cascading the edit onto whichever row now
        # occupies that index.
        row_key = rel.replace("/", "::")
        layout_key = f"layout::{row_key}"

        entry = _screen_entry_for_ref(area_doc, rel)
        ver_labels = _layout_ver_labels_for_entry(entry)
        ver_keys = list(ver_labels.keys())
        show_layout_switch = len(ver_keys) > 1

        if show_layout_switch:
            ctl = st.columns([5, 1.4], vertical_alignment="bottom")
            with ctl[1]:
                _layout_default_idx = (
                    ver_keys.index("default") if "default" in ver_keys else 0
                )
                card_ver = st.selectbox(
                    "Layout",
                    options=ver_keys,
                    format_func=lambda k, labels=ver_labels: labels[k],
                    index=_layout_default_idx,
                    key=layout_key,
                    help="**Auto**: this gallery file + worker-style regions. "
                    "**Default** / **Force vN**: switch ``area.json`` reference "
                    "(``ocr`` / ``versions[].ocr``) and regions.",
                )
        else:
            card_ver = "auto"

        layout_rel = _display_ref_rel_for_card(area_doc, rel, card_ver)
        labeling_ref = _display_ref_rel_for_card(area_doc, rel, "default")
        labeling_qp = open_in_labeling_query_params(labeling_ref, card_ver)
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
            continue

        regs_f, bbox_regs, sid = _gallery_slice_cached(area_mtime, rel, card_ver)
        regs = set(regs_f)
        extra: list[str] = []
        if (sid or "").strip():
            extra.append(f"page={sid.strip()}")
        if regs:
            extra.append(f"regions={len(regs)}")
        extra_txt = (" · " + " · ".join(extra)) if extra else ""

        # One line: the file actually shown; optional note if the gallery row differs.
        cap_rel = f"`{layout_rel}`"
        if rel != layout_rel:
            cap_rel = f"{cap_rel} · row `{rel}`"

        body = st.columns([5, 1.4], vertical_alignment="top")
        with body[1]:
            highlight = st.toggle(
                "Highlight regions",
                value=False,
                key=f"hl::{row_key}",
                help="Draw `area.json` rectangles on the full image, then scale like the thumb.",
            )
        try:
            if highlight and bbox_regs:
                annotated = _annotate_regions_png(native_png, bbox_regs)
                fitted, native, _disp = png_bytes_fitted(annotated, thumb_max)
            else:
                fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)
        except Exception as exc:
            st.error(f"`{layout_rel}`: load failed: {exc}")
            fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)
        with body[0]:
            if highlight and not bbox_regs:
                st.caption("No region boxes in `area.json` for this layout.")
            st.image(
                fitted,
                caption=f"{cap_rel} · {native[0]}×{native[1]}{extra_txt}",
                width=thumb_max,
            )
        with body[1]:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_raw))
            st.caption(ts)
            st.page_link(
                "views/labeling.py",
                label="Open in Labeling",
                query_params=labeling_qp,
                help="``ref`` = screen **default** ``ocr``; ``version`` matches **Layout** "
                "(``default`` for Auto/Default, or ``vN`` when forcing a version).",
                width="stretch",
            )
            # Single selectbox, identical to the annotator's
            # "Screen ID (node)" on the Labels page — same option source
            # (``screen_id_select_options``) and same formatter
            # (``"" → "None (atypical / not in node graph)"``). Writes
            # ``screen_id`` on the entry that owns this row's ref, exactly
            # like Labels does via ``_render_screen_id_and_ocr_fields``.
            try:
                _sid_opts = screen_id_select_options(area_doc, sid or "")
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
                help=(
                    "Logical game / world node — **not** the PNG filename; "
                    "several reference images can share one node. "
                    "Pick **None** until you map this shot."
                ),
            )

            if (_selected_sid or "") != (sid or ""):
                ok, err = _set_screen_id_for_ref(rel, _selected_sid or "")
                if ok:
                    st.toast(
                        f"`{rel}` → node = "
                        + (_selected_sid or "(none)"),
                        icon="✅",
                    )
                    _load_area_doc_cached.clear()
                    _gallery_slice_cached.clear()
                    st.rerun()
                else:
                    st.error(f"Save failed: {err}")
        st.divider()


if group_by == "page (screen_id)":
    groups: dict[str, list[Path]] = {}
    for p in filtered:
        rel = _rel_under_references(p, ref_root)
        _, _, sid = _gallery_slice_cached(area_mtime, rel, "auto")
        sid_g = _node_group_for(rel, sid)
        groups.setdefault(sid_g, []).append(p)

    for sid in sorted(groups.keys()):
        with st.expander(f"{sid} · {len(groups[sid])}", expanded=False):
            _render_cards(
                groups[sid],
                area_mtime=area_mtime,
                area_doc=area_doc,
            )
else:
    _render_cards(
        filtered,
        area_mtime=area_mtime,
        area_doc=area_doc,
    )

