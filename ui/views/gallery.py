"""Gallery: browse `references/*.png` with filters and grouping."""

from __future__ import annotations

import json
import time
from pathlib import Path

import streamlit as st

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
    """Return (regions_by_ref_rel, screen_id_by_ref_rel) from `area.json`."""
    regions_by_ref: dict[str, set[str]] = {}
    screen_id_by_ref: dict[str, str] = {}

    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return regions_by_ref, screen_id_by_ref

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
        raw_regs = e.get("regions")
        if isinstance(raw_regs, list):
            for r in raw_regs:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip()
                if nm:
                    regs.add(nm)
        if regs:
            regions_by_ref[rel] = regs

        sid = str(e.get("screen_id") or "").strip()
        if sid:
            screen_id_by_ref[rel] = sid

    return regions_by_ref, screen_id_by_ref


st.title("Gallery")

ref_root = references_root()

area_doc = _load_area_doc()
regions_by_ref, screen_id_by_ref = _index_area(area_doc)
all_regions = sorted({r for regs in regions_by_ref.values() for r in regs})

top = st.columns([1.1, 1.0, 1.0, 1.0, 2.2], vertical_alignment="bottom")
with top[0]:
    limit = int(
        st.number_input(
            "Max files (newest-first)",
            min_value=50,
            max_value=20000,
            value=2000,
            step=50,
            help="Loads up to this many PNGs from disk (newest-first).",
        )
    )
with top[1]:
    cols_n = int(st.number_input("Columns", min_value=2, max_value=8, value=4, step=1))
with top[2]:
    thumb_max = int(st.number_input("Thumb max side", min_value=120, max_value=800, value=260, step=20))
with top[3]:
    group_by = st.selectbox("Group by", options=["none", "page (screen_id)"], index=1)
with top[4]:
    q = st.text_input(
        "Filter by filename",
        value="",
        placeholder="e.g. main_city, isNewPeople, rookie…",
    )

flt = st.columns([1, 1], vertical_alignment="bottom")
with flt[0]:
    show_temporal = st.toggle("Include `references/temporal/`", value=False)
with flt[1]:
    show_crop = st.toggle("Include `references/crop/`", value=False)

region_sel = st.multiselect(
    "Filter by regions (from `area.json`)",
    options=all_regions,
    default=[],
    help="Show screenshots that contain **all** selected regions in `area.json`.",
)

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
        if not want_regions.issubset(have):
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
    cols = st.columns(cols_n)
    for i, p in enumerate(paths):
        col = cols[i % cols_n]
        with col:
            rel = _rel_under_references(p, ref_root)
            try:
                data = p.read_bytes()
                fitted, native, _disp = png_bytes_fitted(data, thumb_max)
                sid = (screen_id_by_ref.get(rel) or "").strip()
                regs = regions_by_ref.get(rel, set())
                extra = []
                if sid:
                    extra.append(f"page={sid}")
                if regs:
                    extra.append(f"regions={len(regs)}")
                extra_txt = (" · " + " · ".join(extra)) if extra else ""
                st.image(
                    fitted,
                    caption=f"`{rel}` · {native[0]}×{native[1]}{extra_txt}",
                    width="stretch",
                )
            except OSError as exc:
                st.error(f"`{rel}`: {exc}")
                continue

            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
            st.caption(ts)
            st.page_link(
                "views/labeling.py",
                label="Open in Labeling",
                query_params={"ref": rel},
                width="stretch",
            )


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

