"""Gallery: browse `references/*.png` with filters and grouping."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw

from layout.area_regions import is_auxiliary_overlay_region
from layout.area_versions import (
    effective_ocr_for_region,
    normalize_version_id,
    resolve_region_with_version,
    split_versioned_name,
)
from ui.preview_display import png_bytes_fitted
from ui.reference_preview import list_reference_pngs, references_root


def _rel_under_references(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


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


def _all_version_ids_from_doc(doc: dict[str, object]) -> list[str]:
    ids: set[str] = set()
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return []
    for e in screens:
        if isinstance(e, dict):
            ids.update(_declared_version_ids(e))

    def sort_key(vid: str) -> tuple[int, str]:
        tail = vid[1:] if vid.startswith("v") else vid
        try:
            return (int(tail), vid)
        except ValueError:
            return (9999, vid)

    return sorted(ids, key=sort_key)


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

    For **forced ``vN``**, skip that filter so ``*_vN`` regions (whose crop lives
    on ``versions[].ocr``) still appear when viewing the default reference image.
    Otherwise switching to “Force v2” on ``main_city.png`` hid ``chapter.task_v2``
    and left only v1 boxes.
    """
    known = _declared_version_ids(entry)
    av = _active_version_for_ref(ref_rel, entry, preview_mode)
    skip_eff_match = preview_mode not in ("auto", "default")
    raw_regs = entry.get("regions")
    if not isinstance(raw_regs, list):
        return []

    bases: set[str] = set()
    for r in raw_regs:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        if not nm:
            continue
        base, _vid = split_versioned_name(nm, known)
        bases.add(base)

    by_name: dict[str, dict[str, object]] = {}
    for base in bases:
        resolved = resolve_region_with_version(entry, base, av)
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


def _primary_region_names_for_filter(doc: dict[str, object]) -> list[str]:
    """Region names for Gallery filter: exclude overlay search ROIs and tap helpers."""
    names: set[str] = set()
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return []
    for e in screens:
        if not isinstance(e, dict):
            continue
        raw_regs = e.get("regions")
        if not isinstance(raw_regs, list):
            continue
        for r in raw_regs:
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

_ver_labels = OrderedDict(
    [
        ("auto", "Auto (match file)"),
        ("default", "Default (v1)"),
    ]
)
for _vid in _all_version_ids_from_doc(area_doc):
    _ver_labels[_vid] = f"Force {_vid}"
_ver_keys = list(_ver_labels.keys())

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

ql = q.strip().lower()
want_regions = {str(x).strip() for x in (region_sel or []) if str(x).strip()}
filtered: list[Path] = []
for p in files:
    rel = _rel_under_references(p, ref_root)
    if ql and ql not in rel.lower():
        continue
    if want_regions:
        have_f, _, _ = _gallery_slice_cached(area_mtime, rel, "auto")
        if not (want_regions & set(have_f)):
            continue
    filtered.append(p)

st.caption(
    f"Showing **{len(filtered)} / {len(files)}** file(s) · root: `{ref_root}` · "
    "per-image **Layout** below · region filter uses **Auto** · newest first · "
    "click **Open in Labeling** to annotate."
)

if not filtered:
    st.info("No PNGs found under `references/` (excluding `temporal/` and `crop/`).")
    st.stop()

def _render_cards(
    paths: list[Path],
    *,
    key_prefix: str,
    area_mtime: float,
    area_doc: dict[str, object],
    ver_keys: list[str],
    ver_labels: OrderedDict[str, str],
) -> None:
    for i, p in enumerate(paths):
        rel = _rel_under_references(p, ref_root)
        layout_key = f"layout::{key_prefix}::{i}"

        ctl = st.columns([5, 1.4], vertical_alignment="bottom")
        with ctl[1]:
            card_ver = st.selectbox(
                "Layout",
                options=ver_keys,
                format_func=lambda k: ver_labels[k],
                index=0,
                key=layout_key,
                help="**Auto**: this gallery file + worker-style regions. "
                "**Default** / **Force vN**: switch to the matching ``area.json`` "
                "reference image (``ocr`` / ``versions[].ocr``) and regions.",
            )

        layout_rel = _display_ref_rel_for_card(area_doc, rel, card_ver)
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
        if card_ver != "auto":
            extra.append(f"ver={card_ver}")
        if layout_rel != rel:
            extra.append(f"image={layout_rel}")
        extra_txt = (" · " + " · ".join(extra)) if extra else ""

        cap_rel = f"`{rel}`" if layout_rel == rel else f"gallery `{rel}` · **`{layout_rel}`**"

        body = st.columns([5, 1.4], vertical_alignment="top")
        with body[1]:
            highlight = st.toggle(
                "Highlight regions",
                value=False,
                key=f"hl::{key_prefix}::{i}",
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
                query_params={"ref": layout_rel},
                width="stretch",
            )
        st.divider()


if group_by == "page (screen_id)":
    groups: dict[str, list[Path]] = {}
    for p in filtered:
        rel = _rel_under_references(p, ref_root)
        _, _, sid = _gallery_slice_cached(area_mtime, rel, "auto")
        sid_g = (sid or "").strip() or "(unassigned)"
        groups.setdefault(sid_g, []).append(p)

    for sid in sorted(groups.keys()):
        with st.expander(f"{sid} · {len(groups[sid])}", expanded=False):
            _render_cards(
                groups[sid],
                key_prefix=f"grp::{sid}",
                area_mtime=area_mtime,
                area_doc=area_doc,
                ver_keys=_ver_keys,
                ver_labels=_ver_labels,
            )
else:
    _render_cards(
        filtered,
        key_prefix="flat",
        area_mtime=area_mtime,
        area_doc=area_doc,
        ver_keys=_ver_keys,
        ver_labels=_ver_labels,
    )

