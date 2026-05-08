"""Gallery: browse `references/*.png` with filters and grouping."""

from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw

from layout.area_regions import is_auxiliary_overlay_region
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


def _load_area_doc() -> dict[str, object]:
    area_path = Path(__file__).resolve().parents[2] / "area.json"
    try:
        mtime = area_path.stat().st_mtime if area_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return _load_area_doc_cached(mtime)


def _index_area(doc: dict[str, object]) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return (regions_by_ref_rel, screen_id_by_ref_rel, region_bbox_by_ref_rel) from `area.json`."""
    regions_by_ref: dict[str, set[str]] = {}
    screen_id_by_ref: dict[str, str] = {}
    region_bbox_by_ref: dict[str, list[dict[str, object]]] = {}

    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return regions_by_ref, screen_id_by_ref, region_bbox_by_ref

    for e in screens:
        if not isinstance(e, dict):
            continue
        ocr = str(e.get("ocr") or "").replace("\\", "/").strip()
        if not ocr:
            continue
        try:
            rel = Path(ocr).relative_to("references").as_posix()
        except Exception:
            rel = Path(ocr).name

        regs: set[str] = set()
        bbox_entries: list[dict[str, object]] = []
        raw_regs = e.get("regions")
        if isinstance(raw_regs, list):
            for r in raw_regs:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip()
                if nm:
                    regs.add(nm)
                bb = r.get("bbox")
                if nm and isinstance(bb, dict):
                    bbox_entries.append({"name": nm, "bbox": bb})
        if regs:
            regions_by_ref[rel] = regs
        if bbox_entries:
            region_bbox_by_ref[rel] = bbox_entries

        sid = str(e.get("screen_id") or "").strip()
        if sid:
            screen_id_by_ref[rel] = sid

    return regions_by_ref, screen_id_by_ref, region_bbox_by_ref


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
    """Red outlines from ``area.json`` bboxes (native resolution, then same downscale as plain thumb)."""
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

area_doc = _load_area_doc()
regions_by_ref, screen_id_by_ref, region_bbox_by_ref = _index_area(area_doc)
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

ql = q.strip().lower()
want_regions = {str(x).strip() for x in (region_sel or []) if str(x).strip()}
filtered: list[Path] = []
for p in files:
    rel = _rel_under_references(p, ref_root)
    if ql and ql not in rel.lower():
        continue
    if want_regions:
        have = regions_by_ref.get(rel, set())
        if not (want_regions & have):
            continue
    filtered.append(p)

st.caption(
    f"Showing **{len(filtered)} / {len(files)}** file(s) · root: `{ref_root}` · newest first · "
    "click **Open in Labeling** to annotate."
)

if not filtered:
    st.info("No PNGs found under `references/` (excluding `temporal/` and `crop/`).")
    st.stop()

def _render_cards(paths: list[Path], *, key_prefix: str) -> None:
    for i, p in enumerate(paths):
        rel = _rel_under_references(p, ref_root)
        try:
            data = p.read_bytes()
            native_png = data
            sid = (screen_id_by_ref.get(rel) or "").strip()
            regs = regions_by_ref.get(rel, set())
            bbox_regs = region_bbox_by_ref.get(rel, [])
            extra = []
            if sid:
                extra.append(f"page={sid}")
            if regs:
                extra.append(f"regions={len(regs)}")
            extra_txt = (" · " + " · ".join(extra)) if extra else ""
        except OSError as exc:
            st.error(f"`{rel}`: {exc}")
            continue

        left, right = st.columns([5, 1.4], vertical_alignment="top")
        with right:
            highlight = st.toggle(
                "Highlight regions",
                value=False,
                key=f"hl::{key_prefix}::{i}::{rel}",
                help="Draw region rectangles from `area.json` on the full-size image, then scale to the thumb.",
            )
        try:
            if highlight and bbox_regs:
                annotated = _annotate_regions_png(native_png, bbox_regs)
                fitted, native, _disp = png_bytes_fitted(annotated, thumb_max)
            else:
                fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)
        except Exception as exc:
            st.error(f"`{rel}`: load failed: {exc}")
            fitted, native, _disp = png_bytes_fitted(native_png, thumb_max)
        with left:
            if highlight and not bbox_regs:
                st.caption("No region boxes in `area.json` for this file.")
            st.image(
                fitted,
                caption=f"`{rel}` · {native[0]}×{native[1]}{extra_txt}",
                width=thumb_max,
            )
        with right:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
            st.caption(ts)
            st.page_link(
                "views/labeling.py",
                label="Open in Labeling",
                query_params={"ref": rel},
                width="stretch",
            )
        st.divider()


if group_by == "page (screen_id)":
    groups: dict[str, list[Path]] = {}
    for p in filtered:
        rel = _rel_under_references(p, ref_root)
        sid = (screen_id_by_ref.get(rel) or "").strip() or "(unassigned)"
        groups.setdefault(sid, []).append(p)

    for sid in sorted(groups.keys()):
        with st.expander(f"{sid} · {len(groups[sid])}", expanded=(sid != "(unassigned)")):
            _render_cards(groups[sid], key_prefix=f"grp::{sid}")
else:
    _render_cards(filtered, key_prefix="flat")

