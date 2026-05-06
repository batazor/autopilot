"""
OCR region annotator UI (ADB screenshot + drawable canvas + screen FSM).

Used by the main dashboard (`ui/app.py`) and optionally by ``streamlit run app.py``.
``area.json`` lives in the **repository root** (parent of ``ui/``).
"""

from __future__ import annotations

import io
import json
import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import streamlit as st
from PIL import Image

from ui.streamlit_canvas_compat import ensure_drawable_canvas_compat

ensure_drawable_canvas_compat()

from streamlit_drawable_canvas import st_canvas

from capture.adb_screencap import DEFAULT_ADB_BIN
from layout.area_regions import validate_unique_region_names
from ui.settings_state import get_ui_adb_bin, get_ui_adb_serial
from ui.labeling_reference_panel import (
    labeling_forced_reference_rel,
    labeling_resolve_sel,
    render_labeling_reference_column,
)

# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------


class BBoxDict(TypedDict):
    x: float
    y: float
    width: float
    height: float
    rotation: float
    original_width: int
    original_height: int


class RegionDict(TypedDict, total=False):
    name: str
    action: str
    type: str
    threshold: float
    bbox: BBoxDict


class AreaEntryDict(TypedDict, total=False):
    id: int
    ocr: str
    """Game / world screen id in the FSM (same logical screen → many PNGs across worlds). Empty if unset."""
    screen_id: str
    regions: list[RegionDict]


class FSMDict(TypedDict, total=False):
    """Directed transitions between game screens (FSM topology)."""

    initial_screen: str
    transitions: list[dict[str, str]]


class AreaDocDict(TypedDict, total=False):
    version: int
    fsm: FSMDict
    screens: list[AreaEntryDict]


# -----------------------------------------------------------------------------
# Paths & constants
# -----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
AREA_JSON_PATH = REPO_ROOT / "area.json"
REFERENCES_DIR = REPO_ROOT / "references"
CROP_DIR = REFERENCES_DIR / "crop"
ACTIONS = ("text", "exist", "color_check", "click")
TYPES = ("integer", "string", "boolean")
CANVAS_VERSION = "4.4.6"
# Drawable canvas display size (longer side cap); no separate zoom control.
CANVAS_DISPLAY_MAX_SIDE = 1280


# -----------------------------------------------------------------------------
# Core functions (requested API)
# -----------------------------------------------------------------------------


def capture_screenshot(
    dest: Path, adb_bin: str = DEFAULT_ADB_BIN, serial: str | None = None
) -> tuple[bool, str]:
    """Run ``adb exec-out screencap -p`` and write PNG bytes to ``dest``."""
    from capture.adb_screencap import adb_screencap_to_file

    return adb_screencap_to_file(dest, adb_bin=adb_bin, serial=serial)


def convert_bbox(
    left: float,
    top: float,
    width: float,
    height: float,
    canvas_w: int,
    canvas_h: int,
    orig_w: int,
    orig_h: int,
    rotation: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> BBoxDict:
    """Map canvas-space rectangle to percentage bbox plus original image dimensions."""
    w = max(0.0, float(width) * scale_x)
    h = max(0.0, float(height) * scale_y)
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError("canvas dimensions must be positive")
    return BBoxDict(
        x=100.0 * float(left) / canvas_w,
        y=100.0 * float(top) / canvas_h,
        width=100.0 * w / canvas_w,
        height=100.0 * h / canvas_h,
        rotation=float(rotation),
        original_width=int(orig_w),
        original_height=int(orig_h),
    )


def crop_region(
    image: Image.Image,
    left: float,
    top: float,
    width: float,
    height: float,
) -> Image.Image:
    """Crop ``image`` using pixel coordinates (same space as canvas / resized background)."""
    L = int(math.floor(left))
    T = int(math.floor(top))
    R = int(math.ceil(left + width))
    B = int(math.ceil(top + height))
    W, Ht = image.size
    L = max(0, min(L, W - 1))
    T = max(0, min(T, Ht - 1))
    R = max(L + 1, min(R, W))
    B = max(T + 1, min(B, Ht))
    return image.crop((L, T, R, B))


def _safe_crop_filename_part(name: str, fallback: str) -> str:
    raw = (name or "").strip() or fallback
    out = re.sub(r"[^\w\-.]+", "_", raw)
    out = out.strip("._-") or "region"
    return out[:120]


def export_region_crops(
    pil_original: Image.Image,
    reference_repo_rel: str,
    regions: list[RegionDict],
    *,
    progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Write ``references/crop/<reference_stem>_<region_name>.png`` for each region with a bbox.

    ``progress`` receives fraction in ``[0.0, 1.0]`` after each file is written (optional UI hook).
    """
    stem = Path(reference_repo_rel).stem
    if not stem:
        raise ValueError("Invalid reference path for crop export.")
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    ow, oh = pil_original.size
    indexed = [(i, r) for i, r in enumerate(regions) if r.get("bbox")]
    total = len(indexed)
    written: list[Path] = []
    if progress is not None and total > 0:
        progress(0.0)
    for step, (i, reg) in enumerate(indexed):
        bbox = reg.get("bbox") or {}
        label = _safe_crop_filename_part(str(reg.get("name", "")), f"region_{i}")
        left = bbox["x"] / 100.0 * ow
        top = bbox["y"] / 100.0 * oh
        w = bbox["width"] / 100.0 * ow
        h = bbox["height"] / 100.0 * oh
        tile = crop_region(pil_original, left, top, w, h)
        dest = CROP_DIR / f"{stem}_{label}.png"
        tile.save(dest, format="PNG")
        written.append(dest)
        if progress is not None and total > 0:
            progress(min(1.0, (step + 1) / total))
    return written


def default_area_doc(screens: list[AreaEntryDict] | None = None) -> AreaDocDict:
    return AreaDocDict(
        version=2,
        fsm=FSMDict(initial_screen="", transitions=[]),
        screens=list(screens or []),
    )


def normalize_area_file(raw: Any) -> AreaDocDict:
    """Accept legacy ``[ {...}, ... ]`` or ``{ "screens": [...], "fsm": {...} }``."""
    if isinstance(raw, list):
        return default_area_doc(raw)  # type: ignore[arg-type]

    if isinstance(raw, dict):
        screens = raw.get("screens")
        if not isinstance(screens, list):
            raise ValueError("area.json object must include a 'screens' array")
        fsm_raw = raw.get("fsm") if isinstance(raw.get("fsm"), dict) else {}
        trans_raw = fsm_raw.get("transitions")
        transitions: list[dict[str, str]] = []
        if isinstance(trans_raw, list):
            for item in trans_raw:
                if not isinstance(item, dict):
                    continue
                fr = str(item.get("from", "")).strip()
                to = str(item.get("to", "")).strip()
                if not fr or not to:
                    continue
                transitions.append(
                    {
                        "from": fr,
                        "to": to,
                        "event": str(item.get("event", "") or "").strip(),
                    }
                )
        return AreaDocDict(
            version=int(raw.get("version", 2)),
            fsm=FSMDict(
                initial_screen=str(fsm_raw.get("initial_screen", "") or "").strip(),
                transitions=transitions,
            ),
            screens=screens,  # type: ignore[arg-type]
        )

    raise ValueError("area.json must be a JSON array or an object with 'screens'")


def save_json(path: Path, doc: AreaDocDict) -> None:
    """Write ``area.json`` (wrapped format with ``fsm`` + ``screens``)."""
    validate_unique_region_names(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def load_json(path: Path) -> AreaDocDict:
    if not path.exists():
        return default_area_doc([])
    raw = json.loads(path.read_text(encoding="utf-8"))
    return normalize_area_file(raw)


def try_import_bot_fsm_transitions() -> list[dict[str, str]]:
    """Edges from :mod:`navigation.fsm_screen_map` when running inside the repo."""
    try:
        from navigation.fsm_screen_map import FSM_SCREEN_EDGES
    except ImportError:
        return []
    out: list[dict[str, str]] = []
    for src, dsts in FSM_SCREEN_EDGES.items():
        for dst in dsts:
            out.append({"from": str(src), "to": str(dst), "event": ""})
    return out


def dedupe_transitions(transitions: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for t in transitions:
        key = (t.get("from", ""), t.get("to", ""), t.get("event", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(t))
    return out


def all_fsm_screen_ids(doc: AreaDocDict) -> list[str]:
    ids: set[str] = set()
    for s in doc.get("screens") or []:
        sid = str(s.get("screen_id", "") or "").strip()
        if sid:
            ids.add(sid)
    fsm = doc.get("fsm") or {}
    for t in fsm.get("transitions") or []:
        ids.add(str(t.get("from", "")).strip())
        ids.add(str(t.get("to", "")).strip())
    ini = str(fsm.get("initial_screen", "") or "").strip()
    if ini:
        ids.add(ini)
    return sorted(ids)


def successor_screens(screen_id: str, doc: AreaDocDict) -> list[str]:
    if not screen_id.strip():
        return []
    fsm = doc.get("fsm") or {}
    hits = sorted(
        {str(t.get("to")) for t in (fsm.get("transitions") or []) if t.get("from") == screen_id}
    )
    return hits


def screen_id_select_options(doc: AreaDocDict, current_screen_id: str) -> list[str]:
    """Options for Screen ID: ``""`` = None; then sorted ids from FSM + entries (always includes ``current``)."""
    ids: set[str] = set(all_fsm_screen_ids(doc))
    cur = (current_screen_id or "").strip()
    if cur:
        ids.add(cur)
    return [""] + sorted(x for x in ids if x)


def _format_screen_id_choice(value: str) -> str:
    if value == "":
        return "None (atypical / not in FSM)"
    return value


def _render_screen_id_and_ocr_fields(
    doc: AreaDocDict,
    entries: list[AreaEntryDict],
    entry_idx: int,
    *,
    labeling_mode: bool,
) -> None:
    """Screen ID + OCR path + transition hints (shared by left panel or Labeling right panel)."""
    if not entries or entry_idx < 0 or entry_idx >= len(entries):
        return
    cur = entries[entry_idx]
    sid_default = str(cur.get("screen_id", "") or "").strip()
    sid_opts = screen_id_select_options(doc, sid_default)
    try:
        sid_index = sid_opts.index(sid_default)
    except ValueError:
        sid_index = 0
    _sk = "lbl" if labeling_mode else "std"
    screen_id = st.selectbox(
        "Screen ID (FSM state)",
        options=sid_opts,
        index=sid_index,
        format_func=_format_screen_id_choice,
        key=f"screen_id_{entry_idx}_{_sk}",
        help=(
            "Logical game / world screen from your FSM — **not** the PNG filename; "
            "several reference images can share one FSM state. "
            "Pick **None** until you map this shot to a screen."
        ),
    )
    cur["screen_id"] = str(screen_id).strip()
    ocr_path = st.text_input(
        "OCR image path (JSON)",
        value=str(cur.get("ocr", "")),
        key=f"ocr_{entry_idx}_{_sk}",
        disabled=labeling_mode,
        help="Set by the Labeling file tree when editing from the dashboard." if labeling_mode else None,
    )
    if not labeling_mode:
        cur["ocr"] = ocr_path.strip()
    sid = str(cur.get("screen_id", "") or "").strip()
    if sid:
        nxt = successor_screens(sid, doc)
        if nxt:
            st.caption("Transitions from this screen: **" + "**, **".join(nxt) + "**")
        else:
            st.caption("No outgoing edges for this `screen_id` — add them in the FSM section below.")


def _render_fsm_expander(doc: AreaDocDict) -> None:
    """Directed transitions editor (shared layout)."""
    with st.expander("FSM / screen transitions", expanded=False):
        if "fsm" not in doc or not isinstance(doc.get("fsm"), dict):
            doc["fsm"] = {"initial_screen": "", "transitions": []}
        fsm = doc["fsm"]
        if fsm.get("transitions") is None:
            fsm["transitions"] = []
        cur_ini = str(fsm.get("initial_screen", "") or "")
        id_set = set(all_fsm_screen_ids(doc))
        if cur_ini:
            id_set.add(cur_ini)
        ids_opts = [""] + sorted(id_set)
        ini_pick = st.selectbox(
            "Initial screen",
            options=ids_opts,
            index=ids_opts.index(cur_ini) if cur_ini in ids_opts else 0,
            help="Entry point in the graph (start screen for annotation).",
        )
        fsm["initial_screen"] = ini_pick.strip()

        st.markdown("**Add transition** `from → to`")
        c1, c2, c3 = st.columns(3)
        with c1:
            nf = st.text_input("from", key="fsm_new_from", placeholder="main_city")
        with c2:
            nt = st.text_input("to", key="fsm_new_to", placeholder="arena")
        with c3:
            ne = st.text_input("event (optional)", key="fsm_new_ev", placeholder="tap_arena")
        if st.button("Add transition"):
            if nf.strip() and nt.strip():
                fsm["transitions"].append(
                    {"from": nf.strip(), "to": nt.strip(), "event": ne.strip()}
                )
                fsm["transitions"] = dedupe_transitions(fsm["transitions"])
                st.rerun()
            else:
                st.warning("Set both from and to.")

        bot_n = len(try_import_bot_fsm_transitions())
        if st.button(
            f"Import edges from bot (fsm_screen_map), count: {bot_n}",
            disabled=bot_n == 0,
            help="Run from repo root with the package on PYTHONPATH.",
        ):
            merged = list(fsm["transitions"]) + try_import_bot_fsm_transitions()
            fsm["transitions"] = dedupe_transitions(merged)
            st.rerun()

        trans = fsm["transitions"]
        if trans:
            rm_labels = [f"{i}: {t.get('from')} → {t.get('to')}" for i, t in enumerate(trans)]
            kill = st.multiselect(
                "Remove transitions", options=list(range(len(trans))), format_func=lambda i: rm_labels[i]
            )
            if st.button("Remove selected"):
                keep = [t for i, t in enumerate(trans) if i not in set(kill)]
                fsm["transitions"] = keep
                st.rerun()

        with st.expander("Mermaid (copy into a diagram editor)"):
            st.code(transitions_to_mermaid(doc), language="text")


def _render_regions_expander(
    pil_original: Image.Image | None,
    canvas_max_side: int,
    *,
    labeling_mode: bool,
) -> None:
    """Regions list, metadata, crop preview; **Add region** at top."""
    _rk = "lbl" if labeling_mode else "std"
    with st.expander("Regions", expanded=True):
        if st.button("Add region", key=f"area_add_region_{_rk}", width="stretch"):
            regs = current_regions()
            ow, oh = 720, 1280
            pil_i: Image.Image | None = st.session_state.get("pil_original")
            if pil_i is not None:
                ow, oh = pil_i.size
            regs.append(_default_region(ow, oh))
            set_current_regions(regs)
            st.session_state.canvas_rev += 1
            st.session_state.selected_region_idx = len(regs) - 1
            st.rerun()

        regions = current_regions()
        if not regions:
            if not labeling_mode:
                st.caption("No regions yet — draw on the canvas or click **Add region**.")
            return

        names = [f"{i}: {r.get('name', '')}" for i, r in enumerate(regions)]
        r_sel = st.radio(
            "Select region",
            range(len(names)),
            format_func=lambda i: names[i],
            index=min(st.session_state.selected_region_idx, len(names) - 1),
            horizontal=False,
        )
        st.session_state.selected_region_idx = int(r_sel)

        idx = int(st.session_state.selected_region_idx)
        reg = regions[idx]

        with st.form(f"reg_edit_{idx}_{_rk}", clear_on_submit=False):
            name = st.text_input("name", value=reg.get("name", ""))
            action = st.selectbox(
                "action",
                ACTIONS,
                index=ACTIONS.index(reg["action"]) if reg.get("action") in ACTIONS else 0,
            )
            rtype = st.selectbox(
                "type",
                TYPES,
                index=TYPES.index(reg["type"]) if reg.get("type") in TYPES else 1,
            )
            threshold = st.number_input(
                "threshold", min_value=0.0, max_value=1.0, value=float(reg.get("threshold", 0.9)), step=0.05
            )
            if st.form_submit_button("Apply edits"):
                reg["name"] = name.strip() or reg.get("name", "region")
                reg["action"] = action
                reg["type"] = rtype
                reg["threshold"] = threshold
                set_current_regions(regions)
                st.success("Saved region metadata.")

        if st.button("Delete region", key=f"del_region_{_rk}"):
            del regions[idx]
            set_current_regions(regions)
            st.session_state.selected_region_idx = max(0, idx - 1)
            st.session_state.canvas_rev += 1
            st.session_state.last_canvas_sig = ""
            st.rerun()

        st.divider()
        st.subheader("Region preview" if labeling_mode else "Preview")
        bbox = reg.get("bbox")
        if pil_original is not None and bbox:
            canvas_img, _ = resize_for_canvas(pil_original, max_side=canvas_max_side)
            cw2, ch2 = canvas_img.size
            left = bbox["x"] / 100.0 * cw2
            top = bbox["y"] / 100.0 * ch2
            w = bbox["width"] / 100.0 * cw2
            h = bbox["height"] / 100.0 * ch2
            crop = crop_region(canvas_img, left, top, w, h)
            st.image(crop, width="stretch")
            if labeling_mode:
                pass
            else:
                st.caption("OCR preview (stub)")
                st.text_area("OCR result", value="(connect your OCR service)", height=68, disabled=True)
        elif not labeling_mode:
            st.caption("Load an image and select a region to preview the crop.")

        if not labeling_mode:
            st.divider()
            st.subheader("Template crops")
            entries_for_crop: list[AreaEntryDict] = st.session_state.area_doc["screens"]
            ei_crop = int(st.session_state.entry_idx)
            ref_rel: str | None = None
            if 0 <= ei_crop < len(entries_for_crop):
                raw_ocr = entries_for_crop[ei_crop].get("ocr")
                ref_rel = str(raw_ocr).strip() if raw_ocr else None
            bbox_n = sum(1 for r in regions if r.get("bbox"))
            st.caption(
                f"**`references/crop/<stem>_<region_name>.png`** — regions with a bbox (overwrites). "
                f"**{bbox_n}** region(s)."
            )
            if bbox_n == 0:
                st.caption("Draw at least one region on the canvas.")
            btn_crop = st.button(
                "Write crops → references/crop/",
                key=f"write_crops_{_rk}",
                width="stretch",
                disabled=pil_original is None or not ref_rel or bbox_n == 0,
            )
            if btn_crop and pil_original is not None and ref_rel:
                prog = st.progress(0)
                try:
                    outs = export_region_crops(
                        pil_original,
                        ref_rel,
                        regions,
                        progress=lambda x: prog.progress(x),
                    )
                    rels = [o.relative_to(REPO_ROOT).as_posix() for o in outs]
                    if rels:
                        st.success("Saved:\n" + "\n".join(f"- `{p}`" for p in rels))
                    else:
                        st.warning("No regions with bbox — nothing written.")
                except OSError as e:
                    st.error(str(e))
                finally:
                    prog.empty()


def transitions_to_mermaid(doc: AreaDocDict) -> str:
    lines = ["flowchart LR"]
    ini = str((doc.get("fsm") or {}).get("initial_screen", "") or "").strip()
    if ini:
        safe = ini.replace('"', "'")
        lines.append(f'  _start((start)) --> "{safe}"')
    for t in (doc.get("fsm") or {}).get("transitions") or []:
        a = str(t.get("from", "")).replace('"', "'")
        b = str(t.get("to", "")).replace('"', "'")
        ev = str(t.get("event", "") or "").strip()
        if not a or not b:
            continue
        if ev:
            ev_safe = ev.replace('"', "'")
            lines.append(f'  "{a}" -->|{ev_safe}| "{b}"')
        else:
            lines.append(f'  "{a}" --> "{b}"')
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _next_entry_id(entries: list[AreaEntryDict]) -> int:
    if not entries:
        return 1
    return max(int(e.get("id", 0)) for e in entries) + 1


def _default_region(orig_w: int, orig_h: int) -> RegionDict:
    bbox = BBoxDict(
        x=10.0,
        y=10.0,
        width=20.0,
        height=10.0,
        rotation=0.0,
        original_width=orig_w,
        original_height=orig_h,
    )
    return RegionDict(
        name="region",
        action="text",
        type="string",
        threshold=0.9,
        bbox=bbox,
    )


def _bbox_to_canvas_rect(
    bbox: BBoxDict, canvas_w: int, canvas_h: int, stroke: str, stroke_width: int = 2
) -> dict[str, Any]:
    left = bbox["x"] / 100.0 * canvas_w
    top = bbox["y"] / 100.0 * canvas_h
    width = bbox["width"] / 100.0 * canvas_w
    height = bbox["height"] / 100.0 * canvas_h
    return {
        "type": "rect",
        "version": CANVAS_VERSION,
        "originX": "left",
        "originY": "top",
        "left": left,
        "top": top,
        "width": max(width, 1.0),
        "height": max(height, 1.0),
        "fill": "rgba(255, 255, 255, 0.05)",
        "stroke": stroke,
        "strokeWidth": stroke_width,
        "angle": bbox.get("rotation", 0.0),
        "scaleX": 1.0,
        "scaleY": 1.0,
    }


def regions_to_initial_drawing(
    regions: list[RegionDict],
    canvas_w: int,
    canvas_h: int,
    selected_idx: int | None,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for i, reg in enumerate(regions):
        bbox = reg.get("bbox")
        if not bbox:
            continue
        stroke = "#22c55e" if i == selected_idx else "#ef4444"
        objects.append(_bbox_to_canvas_rect(bbox, canvas_w, canvas_h, stroke))
    return {"version": CANVAS_VERSION, "objects": objects}


def parse_canvas_rects(json_data: Any) -> list[tuple[float, float, float, float, float, float, float]]:
    """
    Return list of (left, top, width, height, rotation, scaleX, scaleY) from Fabric/json objects.
    """
    if not json_data or not isinstance(json_data, dict):
        return []
    out: list[tuple[float, float, float, float, float, float, float]] = []
    for obj in json_data.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "rect":
            continue
        left = float(obj.get("left", 0.0))
        top = float(obj.get("top", 0.0))
        w = float(obj.get("width", 0.0))
        h = float(obj.get("height", 0.0))
        rot = float(obj.get("angle", 0.0) or 0.0)
        sx = float(obj.get("scaleX", 1.0) or 1.0)
        sy = float(obj.get("scaleY", 1.0) or 1.0)
        out.append((left, top, w, h, rot, sx, sy))
    return out


def sync_regions_from_canvas(
    regions: list[RegionDict],
    json_data: dict[str, Any],
    canvas_w: int,
    canvas_h: int,
    orig_w: int,
    orig_h: int,
) -> list[RegionDict]:
    rects = parse_canvas_rects(json_data)
    if not rects:
        return regions
    new_regions: list[RegionDict] = []
    for i, r in enumerate(rects):
        left, top, w, h, rot, sx, sy = r
        bbox = convert_bbox(left, top, w, h, canvas_w, canvas_h, orig_w, orig_h, rot, sx, sy)
        if i < len(regions):
            base = dict(regions[i])
            base["bbox"] = bbox
            new_regions.append(base)  # type: ignore[arg-type]
        else:
            nr = _default_region(orig_w, orig_h)
            nr["name"] = f"region_{i + 1}"
            nr["bbox"] = bbox
            new_regions.append(nr)
    return new_regions


def resize_for_canvas(im: Image.Image, max_side: int) -> tuple[Image.Image, float]:
    im = im.convert("RGBA")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    return im, scale


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------


def init_session() -> None:
    if "area_doc" not in st.session_state:
        try:
            st.session_state.area_doc = load_json(AREA_JSON_PATH)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            st.session_state.area_doc = default_area_doc([])
            st.session_state.load_error = str(e)
    if "entry_idx" not in st.session_state:
        screens = st.session_state.area_doc.get("screens") or []
        st.session_state.entry_idx = 0 if screens else -1
    if "selected_region_idx" not in st.session_state:
        st.session_state.selected_region_idx = 0
    if "canvas_rev" not in st.session_state:
        st.session_state.canvas_rev = 0
    if "last_canvas_sig" not in st.session_state:
        st.session_state.last_canvas_sig = ""


def ensure_entry(entries: list[AreaEntryDict], idx: int) -> None:
    if idx < 0 or idx >= len(entries):
        return
    entry = entries[idx]
    if "regions" not in entry or entry["regions"] is None:
        entry["regions"] = []


def current_regions() -> list[RegionDict]:
    entries: list[AreaEntryDict] = st.session_state.area_doc["screens"]
    idx: int = st.session_state.entry_idx
    if idx < 0 or idx >= len(entries):
        return []
    ensure_entry(entries, idx)
    return entries[idx]["regions"]  # type: ignore[return-value]


def set_current_regions(regions: list[RegionDict]) -> None:
    entries: list[AreaEntryDict] = st.session_state.area_doc["screens"]
    idx: int = st.session_state.entry_idx
    if idx < 0 or idx >= len(entries):
        return
    entries[idx]["regions"] = regions


def ensure_entry_for_reference_path(entries: list[AreaEntryDict], ocr_repo_rel: str) -> int:
    """Find or create ``area.json`` entry whose ``ocr`` matches ``references/...`` path."""
    ocr_norm = ocr_repo_rel.replace("\\", "/").strip()
    target = (REPO_ROOT / ocr_norm).resolve()
    for i, e in enumerate(entries):
        raw = str(e.get("ocr") or "").strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = REPO_ROOT / p
        try:
            if p.resolve() == target:
                return i
        except OSError:
            continue
    new_e: AreaEntryDict = {
        "id": _next_entry_id(entries),
        "screen_id": "",
        "ocr": ocr_norm,
        "regions": [],
    }
    entries.append(new_e)
    return len(entries) - 1


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------


def render_area_annotator_ui(
    *,
    labeling_mode: bool = False,
    forced_reference_rel: str | None = None,
    labeling_png_bytes: bytes | None = None,
    labeling_canvas_max_side: int | None = None,
    labeling_ref_root: Path | None = None,
    labeling_existing: list[Path] | None = None,
    labeling_instance_id: str | None = None,
) -> None:
    """Full annotator layout (no ``set_page_config`` — caller / host app sets that)."""
    init_session()

    canvas_max_side = (
        int(labeling_canvas_max_side)
        if labeling_mode and labeling_canvas_max_side is not None
        else CANVAS_DISPLAY_MAX_SIDE
    )

    if "load_error" in st.session_state:
        st.warning(f"Could not load area.json: {st.session_state.load_error}")
        del st.session_state.load_error

    doc: AreaDocDict = st.session_state.area_doc
    entries: list[AreaEntryDict] = doc["screens"]

    try:
        validate_unique_region_names(doc)
    except ValueError as e:
        st.error(str(e))

    _emb_labeling = (
        labeling_mode
        and labeling_ref_root is not None
        and labeling_existing is not None
    )
    if labeling_mode:
        if _emb_labeling:
            pass
        elif forced_reference_rel:
            ei_new = ensure_entry_for_reference_path(entries, forced_reference_rel)
            st.session_state.entry_idx = ei_new
            prev_ref = st.session_state.get("_annot_labeling_ref")
            if prev_ref != forced_reference_rel:
                st.session_state._annot_labeling_ref = forced_reference_rel
                st.session_state.canvas_rev += 1
                st.session_state.last_canvas_sig = ""
            if not labeling_png_bytes:
                lp_forced = REPO_ROOT / forced_reference_rel
                if lp_forced.is_file():
                    st.session_state.pending_image_path = str(lp_forced.resolve())
        else:
            st.session_state.pop("pil_original", None)
            st.session_state.pop("_annot_labeling_ref", None)

    if not entries:
        st.session_state.entry_idx = -1
    else:
        ei = st.session_state.entry_idx
        st.session_state.entry_idx = max(0, min(ei, len(entries) - 1))

    entry_idx: int = st.session_state.entry_idx

    if labeling_mode:
        mid_col, right_col = st.columns([2.78, 1.22], gap="small")
    else:
        left_col, mid_col, right_col = st.columns([1.0, 2.2, 1.25], gap="medium")

    # ----- Left: ADB & entry (standalone annotator only) ---------------------
    if not labeling_mode:
        with left_col:
            st.subheader("Device & capture")
            shot_label = st.text_input("Screen name", value="city_main", help="Used in screenshot filename.")
            if st.button("Take screenshot", type="primary", width="stretch"):
                fname = f"{shot_label}_{int(time.time())}.png"
                dest = REFERENCES_DIR / fname
                ok, msg = capture_screenshot(
                    dest,
                    adb_bin=get_ui_adb_bin(),
                    serial=get_ui_adb_serial(),
                )
                if ok:
                    st.success(f"Saved {msg}")
                    st.session_state.pending_image_path = str(dest)
                    ei = st.session_state.entry_idx
                    if entries and 0 <= ei < len(entries):
                        rel = dest.relative_to(REPO_ROOT).as_posix()
                        entries[ei]["ocr"] = rel
                else:
                    st.error(msg)

            st.divider()
            st.subheader("Entries")

            if st.button("New entry", width="stretch"):
                sl = shot_label.strip() or "city_main"
                rel = f"references/{sl}.png"
                new_e: AreaEntryDict = {
                    "id": _next_entry_id(entries),
                    "screen_id": "",
                    "ocr": rel,
                    "regions": [],
                }
                entries.append(new_e)
                st.session_state.entry_idx = len(entries) - 1
                st.session_state.canvas_rev += 1
                st.rerun()

            if entries:
                labels = [
                    (
                        f"id={e.get('id')} [FSM: "
                        f"{(str(e.get('screen_id', '') or '').strip() or 'None')}] "
                        f"— {e.get('ocr', '')}"
                    )
                    for e in entries
                ]
                choice = st.selectbox(
                    "Editing entry",
                    range(len(labels)),
                    format_func=lambda i: labels[i],
                    index=min(st.session_state.entry_idx, len(labels) - 1),
                )
                st.session_state.entry_idx = int(choice)
            else:
                st.caption("Create a new entry to begin.")

            if entries and 0 <= entry_idx < len(entries):
                _render_screen_id_and_ocr_fields(doc, entries, entry_idx, labeling_mode=False)

            _render_fsm_expander(doc)

    # Load image from disk (pending capture or entry OCR path)
    pil_original: Image.Image | None = st.session_state.get("pil_original")
    image_rel = None
    if entries and 0 <= entry_idx < len(entries):
        image_rel = entries[entry_idx].get("ocr")

    load_path: Path | None = None
    loaded_from_labeling = False
    effective_forced_ref = forced_reference_rel

    if _emb_labeling:
        assert labeling_ref_root is not None and labeling_existing is not None
        with right_col:
            render_labeling_reference_column(
                labeling_ref_root,
                labeling_existing,
                labeling_instance_id or "",
            )
        sel_r = labeling_resolve_sel(labeling_ref_root, labeling_existing)
        effective_forced_ref = labeling_forced_reference_rel(sel_r, labeling_existing)
        if effective_forced_ref:
            ei_new = ensure_entry_for_reference_path(entries, effective_forced_ref)
            st.session_state.entry_idx = ei_new
            prev_ref = st.session_state.get("_annot_labeling_ref")
            if prev_ref != effective_forced_ref:
                st.session_state._annot_labeling_ref = effective_forced_ref
                st.session_state.canvas_rev += 1
                st.session_state.last_canvas_sig = ""
            if not labeling_png_bytes:
                lp_forced = REPO_ROOT / effective_forced_ref
                if lp_forced.is_file():
                    st.session_state.pending_image_path = str(lp_forced.resolve())

        entries = doc["screens"]
        entry_idx = int(st.session_state.entry_idx)
        if not entries:
            st.session_state.entry_idx = -1
        else:
            st.session_state.entry_idx = max(0, min(entry_idx, len(entries) - 1))
        entry_idx = int(st.session_state.entry_idx)

        image_rel = None
        if entries and 0 <= entry_idx < len(entries):
            image_rel = entries[entry_idx].get("ocr")

        pending = st.session_state.pop("pending_image_path", None)
        if labeling_png_bytes and effective_forced_ref:
            try:
                pil_original = Image.open(io.BytesIO(labeling_png_bytes)).convert("RGBA")
                st.session_state.pil_original = pil_original
                st.session_state.active_image_path = str(REPO_ROOT / effective_forced_ref)
                loaded_from_labeling = True
            except OSError as e:
                st.session_state.image_error = str(e)

        if not loaded_from_labeling:
            if pending:
                load_path = Path(pending)
            elif image_rel:
                cand = Path(image_rel)
                load_path = cand if cand.is_file() else (REPO_ROOT / cand)

            if load_path and load_path.is_file():
                try:
                    pil_original = Image.open(load_path).convert("RGBA")
                    st.session_state.pil_original = pil_original
                    st.session_state.active_image_path = str(load_path)
                except OSError as e:
                    st.session_state.image_error = str(e)

    else:
        pending = st.session_state.pop("pending_image_path", None)
        if labeling_mode and forced_reference_rel and labeling_png_bytes:
            try:
                pil_original = Image.open(io.BytesIO(labeling_png_bytes)).convert("RGBA")
                st.session_state.pil_original = pil_original
                st.session_state.active_image_path = str(REPO_ROOT / forced_reference_rel)
                loaded_from_labeling = True
            except OSError as e:
                st.session_state.image_error = str(e)

        if not loaded_from_labeling:
            if pending:
                load_path = Path(pending)
            elif image_rel:
                cand = Path(image_rel)
                load_path = cand if cand.is_file() else (REPO_ROOT / cand)

            if load_path and load_path.is_file():
                try:
                    pil_original = Image.open(load_path).convert("RGBA")
                    st.session_state.pil_original = pil_original
                    st.session_state.active_image_path = str(load_path)
                except OSError as e:
                    st.session_state.image_error = str(e)

    if "image_error" in st.session_state:
        st.error(st.session_state.image_error)
        del st.session_state.image_error

    drawing_mode_labeling = "rect"

    # ----- Labeling: canvas left; right column = reference tree + tools + regions + FSM + save -----
    if labeling_mode:
        with right_col:
            with st.expander("Screen entry", expanded=True):
                if not effective_forced_ref:
                    st.caption("Choose a PNG in the reference tree above to edit regions.")
                elif effective_forced_ref and entries:
                    ei_cur = entry_idx
                    if 0 <= ei_cur < len(entries):
                        cur_e = entries[ei_cur]
                        st.caption(
                            f"Entry **id={cur_e.get('id')}** · FSM screen **"
                            f"{(str(cur_e.get('screen_id', '') or '').strip() or 'None')}**"
                        )
                if entries and 0 <= entry_idx < len(entries):
                    _render_screen_id_and_ocr_fields(doc, entries, entry_idx, labeling_mode=True)

            if pil_original is not None:
                regions_ct = current_regions()
                sel_ct = st.session_state.selected_region_idx
                if regions_ct and sel_ct >= len(regions_ct):
                    st.session_state.selected_region_idx = sel_ct = len(regions_ct) - 1
                if regions_ct:
                    tool_l = st.radio(
                        "Canvas tool",
                        ("Move / resize", "Draw new rectangle"),
                        horizontal=True,
                        index=0,
                        key=f"canvas_tool_nonempty_{st.session_state.entry_idx}",
                    )
                else:
                    tool_l = st.radio(
                        "Canvas tool",
                        ("Move / resize", "Draw new rectangle"),
                        horizontal=True,
                        index=1,
                        key=f"canvas_tool_empty_{st.session_state.entry_idx}",
                    )
                drawing_mode_labeling = "transform" if tool_l.startswith("Move") else "rect"

            _render_regions_expander(pil_original, canvas_max_side, labeling_mode=True)
            _render_fsm_expander(doc)

            st.divider()
            if st.button("Save area.json", type="primary", width="stretch", key="save_area_json_lbl"):
                try:
                    save_json(AREA_JSON_PATH, st.session_state.area_doc)
                    st.success(f"Wrote {AREA_JSON_PATH}")
                except (OSError, ValueError) as e:
                    st.error(str(e))

            st.caption(f"File: `{AREA_JSON_PATH}`")

        with mid_col:
            if pil_original is None:
                st.info("Pick a PNG in the right column or use **New screenshot** in the page header.")
            else:
                orig_w, orig_h = pil_original.size
                canvas_img, _ = resize_for_canvas(pil_original, max_side=canvas_max_side)
                canvas_w, canvas_h = canvas_img.size

                regions = current_regions()
                sel = st.session_state.selected_region_idx
                if regions and sel >= len(regions):
                    st.session_state.selected_region_idx = sel = len(regions) - 1
                initial = regions_to_initial_drawing(regions, canvas_w, canvas_h, sel)

                canvas_result = st_canvas(
                    fill_color="rgba(120, 180, 255, 0.15)",
                    stroke_width=2,
                    stroke_color="#e11d48",
                    background_image=canvas_img,
                    update_streamlit=True,
                    height=canvas_h,
                    width=canvas_w,
                    drawing_mode=drawing_mode_labeling,
                    initial_drawing=initial,
                    key=f"canvas_{st.session_state.entry_idx}_{st.session_state.canvas_rev}_{drawing_mode_labeling}",
                )

                if canvas_result and canvas_result.json_data:
                    sig = json.dumps(canvas_result.json_data, sort_keys=True)
                    if sig != st.session_state.last_canvas_sig:
                        st.session_state.last_canvas_sig = sig
                        updated = sync_regions_from_canvas(
                            regions,
                            canvas_result.json_data,
                            canvas_w,
                            canvas_h,
                            orig_w,
                            orig_h,
                        )
                        set_current_regions(updated)

    else:
        # ----- Standalone: canvas center; regions + save right -----
        with mid_col:
            st.subheader("Annotation canvas")

            if pil_original is None:
                st.info("Take a screenshot or set an existing OCR image path, then load it from disk.")
            else:
                orig_w, orig_h = pil_original.size
                canvas_img, _ = resize_for_canvas(pil_original, max_side=canvas_max_side)
                canvas_w, canvas_h = canvas_img.size

                regions = current_regions()
                sel = st.session_state.selected_region_idx
                if regions and sel >= len(regions):
                    st.session_state.selected_region_idx = sel = len(regions) - 1
                initial = regions_to_initial_drawing(regions, canvas_w, canvas_h, sel)

                _tool_help = (
                    "**Move / resize** — click a box, drag to move, drag corners/edges to resize. "
                    "**Draw new rectangle** — click-drag adds another region (does not edit existing)."
                )
                if regions:
                    tool = st.radio(
                        "Canvas tool",
                        ("Move / resize", "Draw new rectangle"),
                        horizontal=True,
                        index=0,
                        key=f"canvas_tool_nonempty_{st.session_state.entry_idx}",
                        help=_tool_help,
                    )
                else:
                    tool = st.radio(
                        "Canvas tool",
                        ("Move / resize", "Draw new rectangle"),
                        horizontal=True,
                        index=1,
                        key=f"canvas_tool_empty_{st.session_state.entry_idx}",
                        help=_tool_help,
                    )
                drawing_mode = "transform" if tool.startswith("Move") else "rect"

                canvas_result = st_canvas(
                    fill_color="rgba(120, 180, 255, 0.15)",
                    stroke_width=2,
                    stroke_color="#e11d48",
                    background_image=canvas_img,
                    update_streamlit=True,
                    height=canvas_h,
                    width=canvas_w,
                    drawing_mode=drawing_mode,
                    initial_drawing=initial,
                    key=f"canvas_{st.session_state.entry_idx}_{st.session_state.canvas_rev}_{drawing_mode}",
                )

                if canvas_result and canvas_result.json_data:
                    sig = json.dumps(canvas_result.json_data, sort_keys=True)
                    if sig != st.session_state.last_canvas_sig:
                        st.session_state.last_canvas_sig = sig
                        updated = sync_regions_from_canvas(
                            regions,
                            canvas_result.json_data,
                            canvas_w,
                            canvas_h,
                            orig_w,
                            orig_h,
                        )
                        set_current_regions(updated)

                st.caption(
                    "Editing borders: switch to **Move / resize**, click the box, then drag edges or corners. "
                    "**Draw new rectangle** only adds boxes. Regions update automatically when the canvas changes."
                )

        with right_col:
            _render_regions_expander(pil_original, canvas_max_side, labeling_mode=False)

            st.divider()
            if st.button("Save area.json", type="primary", width="stretch", key="save_area_json_std"):
                try:
                    save_json(AREA_JSON_PATH, st.session_state.area_doc)
                    st.success(f"Wrote {AREA_JSON_PATH}")
                except (OSError, ValueError) as e:
                    st.error(str(e))

            st.caption(f"File: `{AREA_JSON_PATH}`")

    # Footer: raw JSON
    with st.expander("Current JSON (preview)"):
        st.json(st.session_state.area_doc)
