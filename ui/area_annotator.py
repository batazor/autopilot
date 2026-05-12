"""
OCR region annotator UI (ADB screenshot + drawable canvas + node graph).

Used by the main dashboard (`ui/app.py`) and optionally by ``streamlit run app.py``.
``area.json`` lives in the **repository root** (parent of ``ui/``).
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import cv2
import numpy as np
import streamlit as st
from PIL import Image

from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from navigation.detector import suggest_node_for_image_sync
from ui.streamlit_canvas_compat import ensure_drawable_canvas_compat

ensure_drawable_canvas_compat()

from streamlit_drawable_canvas import st_canvas

from capture.adb_screencap import DEFAULT_ADB_BIN
from layout.area_regions import (
    dedupe_redundant_version_regions,
    get_version_block,
    is_auxiliary_overlay_region,
    validate_unique_region_names,
    validate_versions,
)
from layout.area_versions import (
    VERSION_ID_RE,
    compile_cond,
    next_version_id,
    normalize_version_id,
)
from ui.keys import (
    ACTIVE_IMAGE_PATH,
    ANNOT_LABELING_REF,
    AREA_DELETE_REGION_PENDING_PREFIX,
    AREA_DOC,
    CANVAS_LAST_SIG,
    CANVAS_REV,
    ENTRY_IDX,
    IMAGE_ERROR,
    LABELING_PENDING_CAPTURE_REL,
    LABELING_RENAME_FLASH,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TEMPORAL_REGIONS,
    LOAD_ERROR,
    OVL_YAML_WARN,
    PENDING_IMAGE_PATH,
    PIL_ORIGINAL,
    SELECTED_REGION_IDX,
    SELECTED_REGION_NAME,
)
from ui.labeling_reference_panel import (
    labeling_forced_reference_rel,
    labeling_resolve_sel,
    render_labeling_reference_column,
)
from ui.overlay_yaml_sync import (
    cascade_aux_region_names,
    overlay_search_region_name,
    overlay_tap_region_name,
    rename_findicon_overlay_primary,
    sync_findicon_overlay_aux_keys,
)
from ui.settings_state import get_ui_adb_bin, get_ui_adb_serial

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
    overlay_auxiliary: bool
    """When True, region is optional overlay helper (search/tap ROI) — skip template crop export."""
    has_red_dot: bool
    """Capability flag: this region's bbox can show the in-game red-dot notification badge.

    Enables ``isRedDot: true|false`` on ``match:`` / ``while_match:`` DSL steps.
    Detection is purely programmatic via :mod:`layout.red_dot_detector` — no template,
    no per-region tuning. Without this flag, ``isRedDot`` errors with
    ``red_dot_capability_disabled`` to catch typos / unintended use."""


class VersionDict(TypedDict, total=False):
    """Visual variant of a screen (e.g. ``v2`` for a high-level hero card layout).

    Region overrides for this version live inside the version's own ``regions[]``
    block (no name suffix). Names omitted from the version's regions fall back to
    base; names listed in ``removed[]`` are treated as absent in this version.
    """

    id: str
    """Version id, must match ``^v\\d+$`` (e.g. ``v2``, ``v3``)."""
    cond: str
    """Python expression evaluated against the player's flat state dict; first truthy version wins."""
    ocr: str
    """Optional per-version reference PNG (relative to repo root). When set and active in
    annotator, the canvas loads this image instead of the entry's default ``ocr`` — so
    overrides can be drawn against the actually-shifted layout."""
    regions: list[RegionDict]
    """Override / version-only region entries scoped to this visual variant."""
    removed: list[str]
    """Names of base regions that do not exist in this version."""


class AreaEntryDict(TypedDict, total=False):
    id: int
    ocr: str
    """Game / world node id (same logical node → many PNGs across worlds). Empty if unset."""
    screen_id: str
    regions: list[RegionDict]
    versions: list[VersionDict]


class FSMDict(TypedDict, total=False):
    """Directed transitions between game nodes (node graph topology)."""

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
ACTIONS = ("exist", "text", "color_check", "click")
TYPES = ("integer", "string", "boolean", "time")
COLOR_TYPES = ("red", "blue", "gray", "green")
CANVAS_VERSION = "4.4.6"
# Drawable canvas display size (longer side cap).
CANVAS_DISPLAY_MAX_SIDE = 1280
# Labeling layout: slightly smaller canvas so the reference / regions column gets more width.
LABELING_CANVAS_DISPLAY_MAX_SIDE = 920
# Region preview thumbnail in the Regions expander (longer side cap).
REGION_PREVIEW_MAX_SIDE = 400
CANVAS_IGNORE_STALE_BBOX_SIG = "_canvas_ignore_stale_bbox_sig"
CANVAS_IGNORE_STALE_UNTIL = "_canvas_ignore_stale_until"


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


def _dominant_color_label_for_pil(tile: Image.Image) -> tuple[str, dict[str, float]]:
    """Dominant color bucket for a PIL crop.

    Returns (label, shares) where label ∈ {red, blue, green, gray}.
    """
    try:
        rgba = tile.convert("RGBA")
        arr = np.array(rgba)  # HxWx4 RGBA
        if arr.size <= 0:
            return "gray", {"red": 0.0, "blue": 0.0, "green": 0.0, "gray": 1.0}
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return dominant_color_label_bgr(bgr)
    except Exception:
        return "gray", {"red": 0.0, "blue": 0.0, "green": 0.0, "gray": 1.0}


def _canvas_component_key(*, drawing_mode: str) -> str:
    """Stable key for the drawable canvas component.

    Avoid coupling the key to `entry_idx` so that switching the active image (ref)
    or doing quick `st.rerun()` cycles doesn't generate a burst of "unregistered ComponentInstance"
    warnings from stale frontend messages.
    """
    import hashlib

    active = str(st.session_state.get(ACTIVE_IMAGE_PATH, "") or "")
    rev = int(st.session_state.get(CANVAS_REV, 0) or 0)
    h = hashlib.sha256(active.encode("utf-8")).hexdigest()[:10]
    return f"canvas_{h}_{rev}_{drawing_mode}"


def _safe_crop_filename_part(name: str, fallback: str) -> str:
    raw = (name or "").strip() or fallback
    out = re.sub(r"[^\w\-.]+", "_", raw)
    out = out.strip("._-") or "region"
    return out[:120]


def crop_path_for_entry_region(
    repo_root: Path,
    entry: AreaEntryDict | None,
    region_name: str,
    *,
    active_version: str | None = None,
) -> Path | None:
    """Return the on-disk crop file for ``region_name`` within ``entry``.

    Walks the active version's ``regions[]`` first (when set), falls back to
    the entry's base ``regions[]``. The crop's stem comes from the version's
    ``ocr`` for version-block regions and the entry's default ``ocr`` for base
    regions.

    Returns ``None`` if the entry/region pair has no usable reference image.
    """
    if not isinstance(entry, dict):
        return None
    name = (region_name or "").strip()
    if not name:
        return None

    if active_version:
        ver_block = get_version_block(entry, active_version)
        if ver_block is not None:
            for reg in ver_block.get("regions") or []:
                if isinstance(reg, dict) and str(reg.get("name", "") or "").strip() == name:
                    chosen_ocr = str(ver_block.get("ocr", "") or "").strip() or str(
                        entry.get("ocr") or ""
                    ).strip()
                    return exported_crop_png(repo_root, chosen_ocr, name) if chosen_ocr else None

    for reg in entry.get("regions") or []:
        if isinstance(reg, dict) and str(reg.get("name", "") or "").strip() == name:
            chosen_ocr = str(entry.get("ocr") or "").strip()
            return exported_crop_png(repo_root, chosen_ocr, name) if chosen_ocr else None

    return None


def export_region_crops(
    pil_original: Image.Image,
    reference_repo_rel: str,
    regions: list[RegionDict],
    *,
    repo_root: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Write ``references/crop/<reference_stem>_<region_name>.png`` for each region with a bbox.

    ``progress`` receives fraction in ``[0.0, 1.0]`` after each file is written (optional UI hook).
    """
    root = repo_root or REPO_ROOT
    crop_out_dir = root / "references" / "crop"
    stem = Path(reference_repo_rel).stem
    if not stem:
        raise ValueError("Invalid reference path for crop export.")
    crop_out_dir.mkdir(parents=True, exist_ok=True)
    ow, oh = pil_original.size
    indexed = [
        (i, r)
        for i, r in enumerate(regions)
        if r.get("bbox") and not r.get("overlay_auxiliary")
    ]
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
        dest = crop_out_dir / f"{stem}_{label}.png"
        tile.save(dest, format="PNG")
        written.append(dest)
        if progress is not None and total > 0:
            progress(min(1.0, (step + 1) / total))
    return written


def _count_exportable_crop_regions(regions: list[RegionDict]) -> int:
    return sum(
        1
        for r in regions
        if r.get("bbox") and not r.get("overlay_auxiliary")
    )


def export_all_region_crops_for_area_doc(
    doc: AreaDocDict,
    *,
    repo_root: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> tuple[list[Path], list[str]]:
    """Write template crops for every screen whose ``ocr`` PNG exists on disk.

    Base ``regions[]`` are cropped from the entry's default ``ocr``. Each declared
    version is exported as its own task group: ``versions[V].regions[]`` are cropped
    from that version's ``ocr`` (falling back to the entry's default ``ocr`` if the
    version has no own image), and the crop filename's stem comes from the version's
    image — so v2 crops naturally end up like ``<entry>_v2_<region>.png``, keeping
    them physically separate from base crops.

    Skips ``overlay_auxiliary`` regions (same rules as :func:`export_region_crops`).

    Returns ``(written_paths, warnings)`` — warnings list missing reference files or load errors.
    """
    root = repo_root or REPO_ROOT
    written: list[Path] = []
    warnings: list[str] = []
    screens = doc.get("screens") or []

    tasks: list[tuple[str, list[RegionDict], Path]] = []
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        default_ocr = str(entry.get("ocr") or "").strip()

        base_regions_raw = entry.get("regions")
        base_regions = (
            [r for r in base_regions_raw if isinstance(r, dict)]
            if isinstance(base_regions_raw, list)
            else []
        )
        if default_ocr and base_regions:
            rel = Path(default_ocr)
            abs_path = rel if rel.is_absolute() else (root / rel)
            if not abs_path.is_file():
                warnings.append(f"Skip (missing file): `{default_ocr}`")
            elif _count_exportable_crop_regions(base_regions) > 0:
                tasks.append((default_ocr, base_regions, abs_path))

        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id", "") or "").strip()
            if not vid:
                continue
            ver_regions_raw = ver.get("regions")
            ver_regions = (
                [r for r in ver_regions_raw if isinstance(r, dict)]
                if isinstance(ver_regions_raw, list)
                else []
            )
            if not ver_regions or _count_exportable_crop_regions(ver_regions) == 0:
                continue
            ver_ocr = str(ver.get("ocr", "") or "").strip() or default_ocr
            if not ver_ocr:
                warnings.append(
                    f"Skip version `{vid}`: no reference image (neither version `ocr` nor entry `ocr`)"
                )
                continue
            rel = Path(ver_ocr)
            abs_path = rel if rel.is_absolute() else (root / rel)
            if not abs_path.is_file():
                warnings.append(f"Skip (missing file): `{ver_ocr}`")
                continue
            tasks.append((ver_ocr, ver_regions, abs_path))

    total_files = sum(_count_exportable_crop_regions(regs) for _, regs, _ in tasks)
    done_files = 0

    for ocr_raw, regions, abs_path in tasks:
        n_this = _count_exportable_crop_regions(regions)
        try:
            pil = Image.open(abs_path)
            pil.load()
        except OSError as e:
            warnings.append(f"Could not open `{ocr_raw}`: {e}")
            done_files += n_this
            if progress is not None and total_files > 0:
                progress(min(1.0, done_files / total_files))
            continue

        def _prog_local(
            frac: float, *, _done: int = done_files, _n_this: int = n_this
        ) -> None:
            if progress is None:
                return
            if total_files <= 0:
                progress(1.0)
            else:
                progress(min(1.0, (_done + frac * _n_this) / total_files))

        try:
            outs = export_region_crops(
                pil,
                ocr_raw,
                regions,
                repo_root=root,
                progress=_prog_local,
            )
            written.extend(outs)
        except (OSError, ValueError) as e:
            warnings.append(f"`{ocr_raw}`: {e}")
        finally:
            done_files += n_this
            if progress is not None and total_files > 0:
                progress(min(1.0, done_files / total_files))

    if progress is not None and total_files == 0:
        progress(1.0)

    return written, warnings


def _write_all_region_crops_with_feedback(doc: AreaDocDict) -> None:
    prog = st.progress(0)
    try:
        written, warns = export_all_region_crops_for_area_doc(
            doc,
            repo_root=REPO_ROOT,
            progress=lambda x: prog.progress(x),
        )
        if written:
            st.success(f"Wrote {len(written)} crop(s) to `references/crop/`.")
        else:
            st.warning("No crops written - check reference PNG paths and non-auxiliary regions.")
        if warns:
            with st.expander("Crop export warnings", expanded=False):
                st.markdown("\n".join(f"- {w}" for w in warns))
    except (OSError, ValueError) as e:
        st.error(f"Crop export failed: {e}")
    finally:
        prog.empty()


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


def save_json(path: Path, doc: AreaDocDict) -> int:
    """Write ``area.json`` (wrapped format with ``fsm`` + ``screens``).

    Returns how many version-specific regions were dropped because they matched
    the base region (same geometry/options).
    """
    removed = dedupe_redundant_version_regions(doc)
    validate_unique_region_names(doc)
    validate_versions(doc)
    content = json.dumps(doc, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)
    return removed


def load_json(path: Path) -> AreaDocDict:
    if not path.exists():
        return default_area_doc([])
    raw = json.loads(path.read_text(encoding="utf-8"))
    return normalize_area_file(raw)


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
    """Options for Screen ID: ``""`` = None; then sorted node ids from area + entries (always includes ``current``)."""
    ids: set[str] = set(all_fsm_screen_ids(doc))
    # Seed options with the "known" node ids from runtime navigation/detection,
    # so the dropdown isn't empty before the user defines node transitions.
    try:
        from navigation.detector import ScreenName

        ids.update(
            s.value for s in ScreenName if getattr(s, "value", None) and s != ScreenName.UNKNOWN
        )
    except Exception:
        pass
    try:
        from navigation.screen_graph import EDGE_TAPS

        for a, b in EDGE_TAPS:
            if a:
                ids.add(str(a))
            if b:
                ids.add(str(b))
    except Exception:
        pass
    # Legacy/manual ids used by tasks/analyzers but not in ScreenName yet.
    ids.update({"mail"})
    cur = (current_screen_id or "").strip()
    if cur:
        ids.add(cur)
    return [""] + sorted(x for x in ids if x)


def _format_screen_id_choice(value: str) -> str:
    if value == "":
        return "None (atypical / not in node graph)"
    return value


_NODE_SUGGEST_CACHE_KEY = "_node_suggest_by_path"


def _node_suggest_for_active_image() -> str | None:
    """Best-effort node id for the canvas image, cached per absolute file path.

    The detector is rerun only when the active reference PNG changes (each
    Streamlit interaction would otherwise re-execute it). Returns ``None`` when
    there is no image, the file is missing, or the detector cannot decide.
    """
    pil = st.session_state.get(PIL_ORIGINAL)
    img_path = str(st.session_state.get(ACTIVE_IMAGE_PATH, "") or "").strip()
    if pil is None or not img_path:
        return None
    cache: dict[str, str | None] = st.session_state.setdefault(_NODE_SUGGEST_CACHE_KEY, {})
    if img_path in cache:
        return cache[img_path]
    try:
        with st.spinner("Detecting node from screenshot…"):
            arr = np.array(pil.convert("RGBA"))
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            cache[img_path] = suggest_node_for_image_sync(bgr)
    except Exception:
        cache[img_path] = None
    return cache[img_path]


def _render_screen_id_and_ocr_fields(
    doc: AreaDocDict,
    entries: list[AreaEntryDict],
    entry_idx: int,
    *,
    labeling_mode: bool,
) -> None:
    """Node id + optional OCR path + transition hints (OCR path UI only outside Labeling)."""
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
    selectbox_key = f"screen_id_{entry_idx}_{_sk}"
    if selectbox_key in st.session_state:
        state_screen_id = str(st.session_state.get(selectbox_key, "") or "").strip()
        if state_screen_id not in sid_opts:
            st.session_state[selectbox_key] = sid_default if sid_default in sid_opts else ""
        selectbox_index = None
    else:
        selectbox_index = sid_index

    suggestion = _node_suggest_for_active_image()
    if suggestion:
        if not sid_default:
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            with c1:
                st.info(f"🔍 Auto-detected node: **`{suggestion}`** _(current is empty)_")
            with c2:
                if st.button("Apply", key=f"apply_node_{entry_idx}_{_sk}"):
                    st.session_state[selectbox_key] = suggestion
                    cur["screen_id"] = suggestion
                    st.rerun()
        elif suggestion != sid_default:
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            with c1:
                st.warning(
                    f"🔍 Auto-detected **`{suggestion}`**, but current is "
                    f"**`{sid_default}`** — possible mismatch."
                )
            with c2:
                if st.button("Apply", key=f"apply_node_{entry_idx}_{_sk}"):
                    st.session_state[selectbox_key] = suggestion
                    cur["screen_id"] = suggestion
                    st.rerun()
        else:
            st.caption(f"✓ Auto-detected node: **`{suggestion}`** _(matches current)_")

    screen_id = st.selectbox(
        "Screen ID (node)",
        options=sid_opts,
        index=selectbox_index,
        format_func=_format_screen_id_choice,
        key=selectbox_key,
        help=(
            "Logical game / world node — **not** the PNG filename; "
            "several reference images can share one node. "
            "Pick **None** until you map this shot to a node."
        ),
    )
    cur["screen_id"] = str(screen_id).strip()
    if not labeling_mode:
        ocr_path = st.text_input(
            "OCR image path (JSON)",
            value=str(cur.get("ocr", "")),
            key=f"ocr_{entry_idx}_{_sk}",
        )
        cur["ocr"] = ocr_path.strip()
    sid = str(cur.get("screen_id", "") or "").strip()
    if sid:
        nxt = successor_screens(sid, doc)
        if nxt:
            st.caption("Transitions from this screen: **" + "**, **".join(nxt) + "**")


ACTIVE_VERSION_DEFAULT = "default"


def _active_version_state_key(entry_id: Any) -> str:
    return f"active_version_entry_{entry_id}"


def get_active_version(entry: AreaEntryDict) -> str | None:
    """Active editing version for ``entry`` (``None`` for default).

    Reads from ``st.session_state`` keyed by entry id so switching screen-entries
    in the sidebar restores the previously chosen version for each.
    """
    eid = entry.get("id", "")
    key = _active_version_state_key(eid)
    sel = str(st.session_state.get(key, ACTIVE_VERSION_DEFAULT) or ACTIVE_VERSION_DEFAULT)
    if sel == ACTIVE_VERSION_DEFAULT:
        return None
    declared = {str(v.get("id", "") or "").strip() for v in (entry.get("versions") or []) if isinstance(v, dict)}
    return sel if sel in declared else None


def apply_active_version_from_labeling_query(
    entry: AreaEntryDict | None,
    version_raw: str,
) -> None:
    """Apply Labeling deep-link ``?version=`` (``default`` / empty → implicit default row)."""
    if entry is None:
        return
    eid = entry.get("id", "")
    state_key = _active_version_state_key(eid)
    raw = str(version_raw or "").strip()
    if not raw or raw.lower() == "default":
        st.session_state[state_key] = ACTIVE_VERSION_DEFAULT
        return
    vid = normalize_version_id(raw)
    if vid is None:
        return
    matching_raw: str | None = None
    for v in entry.get("versions") or []:
        if not isinstance(v, dict):
            continue
        rid = str(v.get("id", "") or "").strip()
        if rid and normalize_version_id(rid) == vid:
            matching_raw = rid
            break
    if matching_raw is None:
        return
    st.session_state[state_key] = matching_raw


def _version_obj(entry: AreaEntryDict, version_id: str) -> VersionDict | None:
    for v in entry.get("versions") or []:
        if isinstance(v, dict) and str(v.get("id", "") or "").strip() == version_id:
            return v  # type: ignore[return-value]
    return None


def get_active_version_ocr_override(entry: AreaEntryDict) -> str | None:
    """Repo-relative PNG path for the entry's active version, or ``None`` to inherit default.

    Used by the labeling canvas to load a per-version reference image when one is bound.
    Returns ``None`` when no version is active or its ``ocr`` field is empty.
    """
    av = get_active_version(entry)
    if not av:
        return None
    v = _version_obj(entry, av)
    if v is None:
        return None
    rel = str(v.get("ocr", "") or "").strip()
    return rel or None


def _copy_live_preview_to_version_reference(
    entry: AreaEntryDict,
    version_id: str,
) -> str:
    """Copy the worker's rolling live preview into ``references/<entry_stem>_<vid>.png``.

    Returns the new repo-relative path. Raises ``RuntimeError`` if anything is off — caller is
    expected to fail the whole "Add version" operation rather than create a half-bound version.
    """
    base_ocr = str(entry.get("ocr", "") or "").strip()
    stem = Path(base_ocr).stem if base_ocr else (
        str(entry.get("screen_id", "") or "screen").strip() or "screen"
    )
    target_rel = f"references/{stem}_{version_id}.png"
    dest = (REPO_ROOT / target_rel).resolve()
    if dest.is_file():
        raise RuntimeError(f"target already exists: {target_rel}")

    iid = str(st.session_state.get("labeling_active_instance_id", "") or "").strip()
    if not iid:
        raise RuntimeError("no active labeling instance — cannot locate the live preview")

    from ui.reference_preview import rolling_live_preview_path

    rolling = rolling_live_preview_path(iid)
    if not rolling.is_file():
        raise RuntimeError(f"live preview not found at `{rolling}`")

    import shutil

    shutil.copyfile(rolling, dest)
    return target_rel


def render_active_version_picker(
    entries: list[AreaEntryDict] | None = None,
    entry_idx: int | None = None,
) -> None:
    """Compact selectbox for the active editing version.

    Renders nothing when the entry has no declared versions — the default is
    implicit and a one-option selector adds noise. Reads entry / index from
    session state if not provided so it can be embedded in panels that don't
    have direct access (e.g. the labeling Reference image column).
    """
    if entries is None:
        entries = st.session_state.get(AREA_DOC, {}).get("screens") or []
    if entry_idx is None:
        entry_idx = int(st.session_state.get("entry_idx", -1))
    if not entries or entry_idx < 0 or entry_idx >= len(entries):
        return
    cur = entries[entry_idx]
    versions = cur.get("versions") or []
    declared_ids = [
        str(v.get("id", "") or "").strip()
        for v in versions
        if isinstance(v, dict)
    ]
    declared_ids = [v for v in declared_ids if v]
    if not declared_ids:
        return  # show only when there are alternates to switch to.

    eid = cur.get("id", "")
    state_key = _active_version_state_key(eid)
    options = [ACTIVE_VERSION_DEFAULT, *declared_ids]
    current_sel = str(st.session_state.get(state_key, ACTIVE_VERSION_DEFAULT) or ACTIVE_VERSION_DEFAULT)
    if current_sel not in options:
        current_sel = ACTIVE_VERSION_DEFAULT
        st.session_state[state_key] = current_sel
    sel = st.selectbox(
        "Active editing version",
        options=options,
        index=options.index(current_sel),
        key=f"version_select_compact_{eid}",
        help="Switch to a non-default version to edit its overrides. Default is implicit (base regions[]).",
    )
    st.session_state[state_key] = sel


def _render_versions_block(
    entries: list[AreaEntryDict],
    entry_idx: int,
    *,
    show_active_picker: bool = True,
) -> None:
    """Edit ``versions`` metadata + (optionally) pick the active editing version.

    The active version drives:
      - region-list filtering (only show overrides for the active version + default base regions),
      - auto-suffix of newly-added region names with ``_<version_id>``,
      - which override the worker selects at runtime via ``cond`` evaluation.

    Pass ``show_active_picker=False`` when the picker is rendered elsewhere
    (e.g. in the labeling Reference image panel) to avoid duplicate widgets.
    """
    if not entries or entry_idx < 0 or entry_idx >= len(entries):
        return
    cur = entries[entry_idx]
    versions: list[VersionDict] = list(cur.get("versions") or [])
    eid = cur.get("id", "")
    state_key = _active_version_state_key(eid)

    declared_ids = [str(v.get("id", "") or "").strip() for v in versions if isinstance(v, dict)]
    declared_ids = [v for v in declared_ids if v]
    options = [ACTIVE_VERSION_DEFAULT, *declared_ids]
    current_sel = str(st.session_state.get(state_key, ACTIVE_VERSION_DEFAULT) or ACTIVE_VERSION_DEFAULT)
    if current_sel not in options:
        current_sel = ACTIVE_VERSION_DEFAULT
        st.session_state[state_key] = current_sel

    with st.expander(
        f"Versions ({len(declared_ids)} declared) — active: {current_sel}",
        expanded=(len(declared_ids) == 0 or current_sel != ACTIVE_VERSION_DEFAULT),
    ):
        # Style any button inside a container whose key starts with `version-danger-` red —
        # Streamlit has no destructive button variant, so we lean on the auto-generated
        # `st-key-<container_key>` class that container(key=...) emits.
        st.markdown(
            """
            <style>
            div[class*="st-key-version-danger-"] button {
                background-color: #c62828 !important;
                color: #ffffff !important;
                border: 1px solid #b71c1c !important;
            }
            div[class*="st-key-version-danger-"] button:hover {
                background-color: #b71c1c !important;
                border-color: #8e0000 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.popover("❓ Versions"):
            st.markdown(
                "Multiple visual variants of the same screen. Each version has its own "
                "`regions[]` list (overrides + version-only additions) and an optional "
                "`removed[]` list naming base regions that disappear in that version."
            )
        if show_active_picker:
            sel = st.selectbox(
                "Active editing version",
                options=options,
                index=options.index(current_sel),
                key=f"version_select_{eid}",
                help="Switch to a non-default version to edit its overrides. Default is implicit (base regions[]).",
            )
            st.session_state[state_key] = sel
        else:
            sel = current_sel

        st.markdown("**Add new version**")
        # Bumping `nonce` after a successful add changes the widget keys for the next render —
        # this is the only safe way to reset Streamlit text_input values, since assigning
        # `st.session_state[key] = ...` is forbidden once a widget with that key has rendered.
        nonce_key = f"version_form_nonce_{eid}"
        nonce = int(st.session_state.get(nonce_key, 0))
        suggested_id = next_version_id(declared_ids)
        id_key = f"version_new_id_{eid}_{nonce}"
        cond_key = f"version_new_cond_{eid}_{nonce}"
        cnew_id, cnew_cond = st.columns([1, 3])
        with cnew_id:
            new_id = st.text_input(
                "id",
                value=suggested_id,
                key=id_key,
                help="`v2`, `v3`, … (case-insensitive; bare digits like `2` are accepted).",
            )
        with cnew_cond:
            new_cond = st.text_input(
                "cond",
                key=cond_key,
                placeholder="heroes.norah.level >= 6",
                help="Python expression against the player's flat state dict. First truthy version wins at runtime.",
            )
        if st.button("Add version", key=f"version_add_btn_{eid}_{nonce}"):
            new_id_norm = normalize_version_id(new_id or "")
            new_cond_clean = (new_cond or "").strip()
            err: str | None = None
            if new_id_norm is None or not VERSION_ID_RE.match(new_id_norm):
                err = (
                    f"Version id {new_id!r} must be `vN` where N is a positive integer "
                    "(e.g. `v2`, `v3`). Bare digits and capital `V` are also accepted."
                )
            elif new_id_norm in declared_ids:
                err = f"Version {new_id_norm!r} already exists."
            elif not new_cond_clean:
                err = "cond expression is required."
            else:
                try:
                    compile_cond(new_cond_clean)
                except SyntaxError as exc:
                    err = f"cond syntax error: {exc}"
            if err:
                st.error(err)
            else:
                # Atomic: bind the live preview FIRST. If anything is wrong (worker not running,
                # target file collision), abort the whole add — no half-created version.
                try:
                    new_ocr_rel = _copy_live_preview_to_version_reference(cur, new_id_norm)
                except RuntimeError as exc:
                    st.error(f"Cannot add version {new_id_norm!r}: {exc}")
                    st.stop()
                versions.append({"id": new_id_norm, "cond": new_cond_clean, "ocr": new_ocr_rel})
                cur["versions"] = versions
                st.session_state[state_key] = new_id_norm
                st.session_state[nonce_key] = nonce + 1
                st.success(f"Added version {new_id_norm!r} · bound `{new_ocr_rel}`.")
                st.rerun()

        if sel != ACTIVE_VERSION_DEFAULT:
            st.markdown(f"**Edit version `{sel}`**")
            ver_obj = next((v for v in versions if str(v.get("id", "") or "").strip() == sel), None)
            if ver_obj is not None:
                ver_ocr_cur = str(ver_obj.get("ocr", "") or "").strip()
                inherit_msg = (
                    "Reference image: *inherits from default* — pick a PNG in the tree "
                    "(or take a screenshot), then bind it here."
                )
                st.caption(
                    f"Reference image: `{ver_ocr_cur}`" if ver_ocr_cur else inherit_msg
                )
                cb1, cb2 = st.columns([2, 1])
                with cb1:
                    if st.button(
                        "Use current canvas image as reference",
                        key=f"version_bind_ocr_btn_{eid}_{sel}",
                        help="Bind the PNG currently shown on the canvas as this version's reference image.",
                    ):
                        active_path = str(st.session_state.get("active_image_path", "") or "")
                        if not active_path:
                            st.error("No image is currently loaded on the canvas.")
                        else:
                            try:
                                rel = Path(active_path).resolve().relative_to(REPO_ROOT).as_posix()
                            except ValueError:
                                st.error(
                                    "Active image is outside the repository — only files under "
                                    "`references/` can be bound as version references."
                                )
                            else:
                                ver_obj["ocr"] = rel
                                st.success(f"Bound `{rel}` as reference for version `{sel}`.")
                                st.rerun()
                with cb2:
                    if ver_ocr_cur and st.button(
                        "Clear",
                        key=f"version_clear_ocr_btn_{eid}_{sel}",
                        help="Unbind — version falls back to the entry's default reference image.",
                    ):
                        ver_obj.pop("ocr", None)
                        st.rerun()
                if st.button(
                    "Sync regions from default",
                    key=f"version_sync_btn_{eid}_{sel}",
                    icon=":material/content_copy:",
                    help=(
                        f"Copy every base region into `versions[{sel}].regions[]` "
                        "(skipping ones that already exist and overlay helpers). "
                        "Drag the copies to their new positions on the version's reference image."
                    ),
                ):
                    added, skipped = _sync_default_regions_into_version(cur, sel)
                    if added:
                        st.success(
                            f"Synced {added} region(s) into `{sel}` "
                            f"(skipped {skipped} already-present / aux)."
                        )
                        st.rerun()
                    else:
                        st.info(
                            f"Nothing to sync — every base region already exists in `versions[{sel}].regions[]`."
                        )
                edited_cond = st.text_input(
                    "cond",
                    value=str(ver_obj.get("cond", "") or ""),
                    key=f"version_edit_cond_{eid}_{sel}",
                )
                cs1, cs2 = st.columns(2)
                with cs1:
                    if st.button("Save cond", key=f"version_save_btn_{eid}_{sel}"):
                        edited = (edited_cond or "").strip()
                        if not edited:
                            st.error("cond cannot be empty.")
                        else:
                            try:
                                compile_cond(edited)
                            except SyntaxError as exc:
                                st.error(f"cond syntax error: {exc}")
                            else:
                                ver_obj["cond"] = edited
                                st.success("Saved.")
                with cs2:
                    confirm_key = f"version_delete_confirm_{eid}_{sel}"
                    # `st-key-version-danger` is targeted by CSS injected at the top of this block
                    # so buttons inside become red — Streamlit has no native destructive variant.
                    with st.container(key=f"version-danger-trigger-{eid}-{sel}"):
                        if st.button(
                            "Delete version (and its overrides)",
                            key=f"version_delete_btn_{eid}_{sel}",
                            icon=":material/delete:",
                        ):
                            st.session_state[confirm_key] = True
                    if st.session_state.get(confirm_key):
                        st.warning(
                            f"Delete version `{sel}` and all its overrides for this entry?"
                        )
                        c_yes, c_no = st.columns(2)
                        with c_yes, st.container(key=f"version-danger-confirm-{eid}-{sel}"):
                            if st.button(
                                "Yes, delete",
                                key=f"version_delete_yes_{eid}_{sel}",
                                icon=":material/delete_forever:",
                            ):
                                cur["versions"] = [
                                    v for v in versions if str(v.get("id", "") or "").strip() != sel
                                ]
                                st.session_state[state_key] = ACTIVE_VERSION_DEFAULT
                                st.session_state[confirm_key] = False
                                st.rerun()
                        with c_no:
                            if st.button("Cancel", key=f"version_delete_no_{eid}_{sel}"):
                                st.session_state[confirm_key] = False
                                st.rerun()


def _render_regions_expander(
    pil_original: Image.Image | None,
    canvas_max_side: int,
    *,
    labeling_mode: bool,
) -> None:
    """Regions list, metadata, crop preview; **Add region** at top."""
    _rk = "lbl" if labeling_mode else "std"
    entries_for_ver: list[AreaEntryDict] = st.session_state.area_doc["screens"]
    ei_for_ver: int = st.session_state.entry_idx
    cur_entry = entries_for_ver[ei_for_ver] if 0 <= ei_for_ver < len(entries_for_ver) else None
    active_version: str | None = get_active_version(cur_entry) if cur_entry else None
    declared_version_ids = (
        {str(v.get("id", "") or "").strip() for v in (cur_entry.get("versions") or [])}
        if cur_entry
        else set()
    )
    declared_version_ids = {v for v in declared_version_ids if v}
    with st.expander("Regions", expanded=True):
        if active_version:
            st.caption(
                f"Editing version `{active_version}` — overrides live in `versions[{active_version}].regions[]`. "
                "Regions added here win over base regions with the same name."
            )
        if st.button("Add region", key=f"area_add_region_{_rk}", width="stretch"):
            regs = current_regions()
            ow, oh = 720, 1280
            pil_i: Image.Image | None = st.session_state.get(PIL_ORIGINAL)
            if pil_i is not None:
                ow, oh = pil_i.size
            regs.append(_default_region(ow, oh, name="region"))
            set_current_regions(regs)
            st.session_state.canvas_rev += 1
            st.session_state.selected_region_idx = len(regs) - 1
            st.session_state.selected_region_name = _selected_region_name_from_idx(
                regs, len(regs) - 1
            )
            st.rerun()

        regions = current_regions()
        if not regions:
            if active_version:
                st.caption(
                    f"No overrides for `{active_version}` yet. Click **Add region** to create one, "
                    "or **Sync regions from default** in the Versions panel."
                )
            elif labeling_mode:
                st.caption("No regions yet - click **Add region**.")
            else:
                st.caption("No regions yet — draw on the canvas or click **Add region**.")
            return

        names = [f"{i}: {regions[i].get('name', '')}" for i in range(len(regions))]
        # `selected_region_name` is the canonical selection (canvas clicks update
        # the name; the idx may be stale). Resolve from name, then force the
        # radio's stored value to match — without a `key` Streamlit keeps the
        # widget's previous value across reruns and ignores `index=`, so canvas
        # selections never reach the list.
        radio_key = f"area_region_radio_{_rk}"

        # on_change fires at the start of the rerun caused by the user's click,
        # before `_resolve_selected_region_idx` runs below. Without it, the
        # stale name resolves to the OLD idx and the force-assign on the next
        # line clobbers the radio click before the widget re-renders.
        def _on_region_radio_change(rkey: str = radio_key) -> None:
            new_idx = int(st.session_state.get(rkey, 0) or 0)
            regs_now = current_regions()
            new_idx = max(0, min(new_idx, len(regs_now) - 1)) if regs_now else 0
            st.session_state.selected_region_idx = new_idx
            st.session_state.selected_region_name = _selected_region_name_from_idx(
                regs_now, new_idx
            )

        target_idx = _resolve_selected_region_idx(regions)
        st.session_state[radio_key] = target_idx
        r_sel = st.radio(
            "Select region",
            range(len(names)),
            format_func=lambda i: names[i],
            horizontal=False,
            key=radio_key,
            on_change=_on_region_radio_change,
        )
        st.session_state.selected_region_idx = int(r_sel)
        st.session_state.selected_region_name = _selected_region_name_from_idx(regions, int(r_sel))

        idx = int(st.session_state.selected_region_idx)
        reg = regions[idx]

        with st.form(f"reg_edit_{idx}_{_rk}", clear_on_submit=False):
            name = st.text_input("name", value=reg.get("name", ""))
            action = st.selectbox(
                "action",
                ACTIONS,
                index=ACTIONS.index(reg["action"]) if reg.get("action") in ACTIONS else 0,
            )
            threshold = st.number_input(
                "threshold", min_value=0.0, max_value=1.0, value=float(reg.get("threshold", 0.9)), step=0.05
            )
            if action == "color_check":
                # Show computed dominant color from the current screenshot so the user
                # can see what the region "looks like" right now.
                dom = ""
                share = ""
                bbox = reg.get("bbox") if isinstance(reg.get("bbox"), dict) else None
                if pil_original is not None and bbox is not None:
                    ow, oh = pil_original.size
                    try:
                        left = float(bbox["x"]) / 100.0 * ow
                        top = float(bbox["y"]) / 100.0 * oh
                        w2 = float(bbox["width"]) / 100.0 * ow
                        h2 = float(bbox["height"]) / 100.0 * oh
                        tile = crop_region(pil_original, left, top, w2, h2)
                        dom, shares = _dominant_color_label_for_pil(tile)
                        share = f"{float(shares.get(dom, 0.0)):.3f}"
                    except Exception:
                        dom, share = "", ""
                st.caption(
                    f"Dominant now: **`{dom or '—'}`**"
                    + (f" (share `{share}`)" if share else "")
                    + f" · threshold: **`{float(threshold):.3f}`**"
                )
                # Keep expected color in `type` but label it clearly.
                cur_type = str(reg.get("type") or "").strip().lower()
                idx_type = COLOR_TYPES.index(cur_type) if cur_type in COLOR_TYPES else 0
                rtype = st.selectbox("expected color (type)", COLOR_TYPES, index=idx_type)
            else:
                cur_type = str(reg.get("type") or "").strip().lower()
                idx_type = TYPES.index(cur_type) if cur_type in TYPES else 1
                rtype = st.selectbox("type", TYPES, index=idx_type)
            has_red_dot_chk = st.checkbox(
                "Has red dot",
                value=bool(reg.get("has_red_dot")),
                help=(
                    "Mark this region as one that may show the in-game red-dot "
                    "notification badge. Enables ``isRedDot: true|false`` in DSL "
                    "``match:`` / ``while_match:`` steps. No template/labeling "
                    "needed — detection is purely programmatic (HSV + circularity)."
                ),
            )
            if st.form_submit_button("Apply edits"):
                old_name = str(reg.get("name", "") or "").strip()
                new_name = str(name.strip() or old_name or "region")
                touched_restore: list[tuple[RegionDict, str]] = []

                if (
                    new_name != old_name
                    and old_name
                    and not old_name.endswith("_search")
                    and not old_name.endswith("_tap")
                ):
                    sn_old = overlay_search_region_name(old_name)
                    sn_new = overlay_search_region_name(new_name)
                    for r in regions:
                        rnm = str(r.get("name", "") or "").strip()
                        if rnm == sn_old:
                            touched_restore.append((r, sn_old))
                            r["name"] = sn_new

                touched_restore.append((reg, old_name))
                reg["name"] = new_name
                reg["action"] = action
                reg["type"] = rtype
                reg["threshold"] = threshold
                if has_red_dot_chk:
                    reg["has_red_dot"] = True
                else:
                    reg.pop("has_red_dot", None)
                try:
                    validate_unique_region_names(st.session_state.area_doc)
                except ValueError as e:
                    for rdict, prev_nm in touched_restore:
                        rdict["name"] = prev_nm
                    st.error(str(e))
                else:
                    if (
                        new_name != old_name
                        and old_name
                        and not old_name.endswith("_search")
                        and not old_name.endswith("_tap")
                    ):
                        rename_findicon_overlay_primary(REPO_ROOT, old_name, new_name)
                    if str(st.session_state.get(SELECTED_REGION_NAME) or "").strip() == old_name:
                        st.session_state.selected_region_name = new_name
                    set_current_regions(regions)
                    st.success("Saved region metadata.")

        if labeling_mode:
            ei_ov = int(st.session_state.entry_idx)
            bn_ov = str(reg.get("name", "") or "").strip()
            bbox_src = reg.get("bbox")
            bbox_ok = isinstance(bbox_src, dict)
            if bn_ov and not bn_ov.endswith("_search") and not bn_ov.endswith("_tap"):
                sn_ov = overlay_search_region_name(bn_ov)
                tn_ov = overlay_tap_region_name(bn_ov)
                with st.popover("❓ Overlay rectangles"):
                    st.markdown(
                        f"Optional overlay rectangles — same `ocr` frame as this region. "
                        f"Sliding match uses `{sn_ov}` from `area.json` automatically; "
                        f"clicks land at `{tn_ov}` center (offset from the matched primary) when present. "
                        "YAML cleanup removes obsolete explicit `search_region` keys."
                    )

                def _regions_contains(nm: str) -> bool:
                    return any(str(r.get("name", "") or "").strip() == nm for r in regions)

                ks_ov = f"ovl_aux_s_{ei_ov}_{idx}"
                if ks_ov not in st.session_state:
                    st.session_state[ks_ov] = _regions_contains(sn_ov)
                kt_ov = f"ovl_aux_t_{ei_ov}_{idx}"
                if kt_ov not in st.session_state:
                    st.session_state[kt_ov] = _regions_contains(tn_ov)

                want_s_ov = st.checkbox(
                    f"Search ROI (`{sn_ov}`)",
                    key=ks_ov,
                    help=(
                        f"Larger ROI for sliding template match "
                        f"(saved as `{sn_ov}` in area.json)."
                    ),
                )
                want_t_ov = st.checkbox(
                    f"Tap ROI (`{tn_ov}`)",
                    key=kt_ov,
                    help=(
                        f"Separate click target — overlay engine taps at the center of "
                        f"`{tn_ov}` (offset from the matched primary). "
                        "Independent of the Search ROI."
                    ),
                )

                search_changed = False
                if bbox_ok and want_s_ov != _regions_contains(sn_ov):
                    if want_s_ov:
                        aux_s: RegionDict = {
                            "name": sn_ov,
                            "action": "exist",
                            "type": "string",
                            "threshold": 0.9,
                            "bbox": dict(bbox_src),
                            "overlay_auxiliary": True,
                        }
                        regions.append(aux_s)
                    else:
                        regions[:] = [
                            x
                            for x in regions
                            if str(x.get("name", "") or "").strip() != sn_ov
                        ]
                    search_changed = True

                tap_changed = False
                if bbox_ok and want_t_ov != _regions_contains(tn_ov):
                    if want_t_ov:
                        aux_t: RegionDict = {
                            "name": tn_ov,
                            "action": "click",
                            "type": "string",
                            "threshold": 0.9,
                            "bbox": dict(bbox_src),
                            "overlay_auxiliary": True,
                        }
                        regions.append(aux_t)
                    else:
                        regions[:] = [
                            x
                            for x in regions
                            if str(x.get("name", "") or "").strip() != tn_ov
                        ]
                    tap_changed = True

                if search_changed or tap_changed:
                    try:
                        validate_unique_region_names(st.session_state.area_doc)
                    except ValueError as _e:
                        # Roll back: reload the doc from disk so in-memory state stays consistent.
                        st.session_state.area_doc = load_json(AREA_JSON_PATH)
                        st.session_state.canvas_rev = int(st.session_state.get(CANVAS_REV, 0)) + 1
                        st.session_state.last_canvas_sig = ""
                        st.error(f"Region validation failed — changes discarded: {_e}")
                        st.rerun()
                    set_current_regions(regions)
                    if search_changed:
                        synced = sync_findicon_overlay_aux_keys(
                            REPO_ROOT,
                            bn_ov,
                            use_search=_regions_contains(sn_ov),
                        )
                        if not synced:
                            st.session_state[OVL_YAML_WARN] = (
                                f"No matching ``findIcon`` overlay rule for region `{bn_ov}` in "
                                "`analyze/analyze.yaml` — regions updated; edit YAML by hand."
                            )
                    st.session_state.pop(ks_ov, None)
                    st.session_state.pop(kt_ov, None)
                    st.session_state.canvas_rev += 1
                    st.session_state.last_canvas_sig = ""
                    st.rerun()
            elif (
                not bbox_ok
                and bn_ov
                and not bn_ov.endswith("_search")
                and not bn_ov.endswith("_tap")
            ):
                st.caption("Draw a bbox on this region before enabling overlay Search/Tap ROIs.")

        _del_pending_key = f"{AREA_DELETE_REGION_PENDING_PREFIX}_{_rk}"
        _raw_pending = st.session_state.get(_del_pending_key)
        pending_del_idx: int | None = None
        if isinstance(_raw_pending, int) and 0 <= _raw_pending < len(regions):
            pending_del_idx = _raw_pending
        elif _raw_pending is not None:
            st.session_state.pop(_del_pending_key, None)

        if pending_del_idx is not None:
            _pend = regions[pending_del_idx]
            _pend_nm = str(_pend.get("name") or "").strip() or f"(region {pending_del_idx})"
            existing_names = {
                str(r.get("name") or "").strip() for r in regions if isinstance(r, dict)
            }
            existing_names.discard("")
            cascade_aux = cascade_aux_region_names(_pend_nm, existing_names)
            names_to_remove: set[str] = {_pend_nm, *cascade_aux} if _pend_nm else set()

            entries_for_del: list[AreaEntryDict] = st.session_state.area_doc["screens"]
            ei_for_del = int(st.session_state.entry_idx)
            entry_for_del: AreaEntryDict | None = (
                entries_for_del[ei_for_del]
                if 0 <= ei_for_del < len(entries_for_del)
                else None
            )
            crops_to_remove: list[Path] = []
            for r in regions:
                rn = str(r.get("name") or "").strip()
                if not rn or rn not in names_to_remove:
                    continue
                if r.get("overlay_auxiliary"):
                    continue
                cp = crop_path_for_entry_region(
                    REPO_ROOT,
                    entry_for_del,
                    rn,
                    active_version=active_version,
                )
                if cp is not None and cp.is_file():
                    crops_to_remove.append(cp)

            warn_parts: list[str] = [f"Delete region `{_pend_nm}`?"]
            if cascade_aux:
                cascade_list = ", ".join(f"`{n}`" for n in cascade_aux)
                warn_parts.append(f"Will also remove its overlay helper(s): {cascade_list}.")
            if crops_to_remove:
                crop_list = ", ".join(f"`references/crop/{p.name}`" for p in crops_to_remove)
                warn_parts.append(f"Will also delete crop file(s): {crop_list}.")
            if not cascade_aux and not crops_to_remove:
                warn_parts.append("This removes it from this screen entry.")
            st.warning(" ".join(warn_parts))
            _dc1, _dc2 = st.columns(2)
            with _dc1:
                _confirm_del = st.button(
                    "Confirm delete",
                    type="primary",
                    key=f"del_region_confirm_yes_{_rk}",
                )
            with _dc2:
                if st.button("Cancel", key=f"del_region_confirm_no_{_rk}"):
                    st.session_state.pop(_del_pending_key, None)
                    st.rerun()
            if _confirm_del:
                kept: list[RegionDict] = []
                deleted_names: list[str] = []
                for r in regions:
                    rn = str(r.get("name") or "").strip()
                    if rn and rn in names_to_remove:
                        deleted_names.append(rn)
                        continue
                    kept.append(r)
                regions[:] = kept
                set_current_regions(regions)
                st.session_state.pop(_del_pending_key, None)

                deleted_crops: list[str] = []
                crop_errors: list[str] = []
                for cp in crops_to_remove:
                    try:
                        cp.unlink()
                        deleted_crops.append(cp.name)
                    except OSError as e:
                        crop_errors.append(f"`references/crop/{cp.name}` ({e})")

                # Drop the overlay aux checkbox state for this entry — indices shift after a
                # cascade delete and any leftover ``ovl_aux_*`` keys would now point at the
                # wrong region. They are recomputed on the next render.
                ei_clean = int(st.session_state.entry_idx)
                stale_aux_prefixes = (f"ovl_aux_s_{ei_clean}_", f"ovl_aux_t_{ei_clean}_")
                for k in list(st.session_state.keys()):
                    if isinstance(k, str) and k.startswith(stale_aux_prefixes):
                        st.session_state.pop(k, None)

                cur_sel = str(st.session_state.get(SELECTED_REGION_NAME) or "").strip()
                if cur_sel in names_to_remove:
                    st.session_state.selected_region_name = ""
                st.session_state.selected_region_idx = max(0, pending_del_idx - 1)
                st.session_state.selected_region_name = _selected_region_name_from_idx(
                    regions, int(st.session_state.selected_region_idx)
                )
                st.session_state.canvas_rev += 1
                st.session_state.last_canvas_sig = ""

                flash_parts: list[str] = []
                if len(deleted_names) > 1:
                    flash_parts.append(
                        "Deleted region(s): "
                        + ", ".join(f"`{n}`" for n in deleted_names)
                    )
                if deleted_crops:
                    flash_parts.append(
                        "Removed crop(s): "
                        + ", ".join(f"`references/crop/{n}`" for n in deleted_crops)
                    )
                if crop_errors:
                    flash_parts.append(
                        "Failed to delete crop(s): " + ", ".join(crop_errors)
                    )
                if flash_parts:
                    st.session_state[LABELING_RENAME_FLASH] = ". ".join(flash_parts) + "."
                st.rerun()
        else:
            st.markdown(
                """
                <style>
                div[class*="st-key-region-danger-"] button {
                    background-color: #c62828 !important;
                    color: #ffffff !important;
                    border: 1px solid #b71c1c !important;
                }
                div[class*="st-key-region-danger-"] button:hover {
                    background-color: #b71c1c !important;
                    border-color: #8e0000 !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            with st.container(key=f"region-danger-{_rk}"):
                if st.button(
                    "Delete region",
                    key=f"del_region_{_rk}",
                    icon=":material/delete:",
                ):
                    st.session_state[_del_pending_key] = idx
                    st.rerun()

        st.divider()
        st.subheader("Region preview" if labeling_mode else "Preview")
        reg_for_preview = regions[pending_del_idx] if pending_del_idx is not None else reg
        bbox = reg_for_preview.get("bbox")
        if pil_original is not None and bbox:
            canvas_img, _ = resize_for_canvas(pil_original, max_side=canvas_max_side)
            cw2, ch2 = canvas_img.size
            left = bbox["x"] / 100.0 * cw2
            top = bbox["y"] / 100.0 * ch2
            w = bbox["width"] / 100.0 * cw2
            h = bbox["height"] / 100.0 * ch2
            crop = crop_region(canvas_img, left, top, w, h)
            st.image(cap_preview_image_max_side(crop, REGION_PREVIEW_MAX_SIDE))
            if reg_for_preview.get("has_red_dot"):
                try:
                    arr_rd = np.array(pil_original.convert("RGBA"))
                    bgr_rd = cv2.cvtColor(arr_rd, cv2.COLOR_RGBA2BGR)
                    found_rd = has_red_dot_in_bbox_percent(bgr_rd, bbox)
                except Exception:
                    found_rd = False
                st.caption(
                    "Red dot now: " + ("**`yes`**" if found_rd else "**`no`**")
                )
            if labeling_mode:
                pass
            else:
                st.caption("OCR preview (stub)")
                st.text_area("OCR result", value="(connect your OCR service)", height=68, disabled=True)
        elif not labeling_mode:
            st.caption("Load an image and select a region to preview the crop.")
        elif labeling_mode and reg_for_preview.get("has_red_dot"):
            st.caption("Red dot now: **`—`** _(no image / bbox)_")

        if not labeling_mode:
            st.divider()
            st.subheader("Template crops")
            entries_for_crop: list[AreaEntryDict] = st.session_state.area_doc["screens"]
            ei_crop = int(st.session_state.entry_idx)
            ref_rel: str | None = None
            if 0 <= ei_crop < len(entries_for_crop):
                raw_ocr = entries_for_crop[ei_crop].get("ocr")
                ref_rel = str(raw_ocr).strip() if raw_ocr else None
            bbox_n = sum(1 for r in regions if r.get("bbox") and not r.get("overlay_auxiliary"))
            st.caption(
                f"**`references/crop/<stem>_<region_name>.png`** — template regions with bbox "
                f"(overlay search/tap helpers skipped). **{bbox_n}** region(s)."
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


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _next_entry_id(entries: list[AreaEntryDict]) -> int:
    if not entries:
        return 1
    return max(int(e.get("id", 0)) for e in entries) + 1


def _default_region(orig_w: int, orig_h: int, *, name: str = "region") -> RegionDict:
    bbox = BBoxDict(
        x=10.0,
        y=10.0,
        width=20.0,
        height=10.0,
        rotation=0.0,
        original_width=orig_w,
        original_height=orig_h,
    )
    # Default to ``exist`` (template-match) for newly created regions —
    # that's the action the overwhelming majority of regions in area.json
    # use (icons, buttons, badges). Operators editing text-class regions
    # can still switch to ``text`` in the action selector.
    return RegionDict(
        name=name,
        action="exist",
        type="string",
        threshold=0.9,
        bbox=bbox,
    )


def _sync_default_regions_into_version(
    entry: AreaEntryDict,
    version_id: str,
) -> tuple[int, int]:
    """Copy base regions into ``versions[V].regions[]`` (without suffix).

    Skips overlay auxiliaries (``_search`` / ``_tap``) and regions already
    present in the version block. The user then drags the copies to their new
    positions on the version's reference image.

    Returns ``(added, skipped)``.
    """
    import copy as _copy

    ver_block = get_version_block(entry, version_id)
    if ver_block is None:
        return 0, 0

    base_regions = entry.get("regions") or []
    ver_regions = ver_block.get("regions")
    if not isinstance(ver_regions, list):
        ver_regions = []
        ver_block["regions"] = ver_regions
    existing = {
        str(r.get("name", "") or "").strip()
        for r in ver_regions
        if isinstance(r, dict)
    }

    added = 0
    skipped = 0
    for r in base_regions:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "") or "").strip()
        if not name:
            continue
        if is_auxiliary_overlay_region(r):
            skipped += 1
            continue
        if name in existing:
            skipped += 1
            continue
        ver_regions.append(_copy.deepcopy(r))
        existing.add(name)
        added += 1

    return added, skipped


def _bbox_to_canvas_rect(
    bbox: BBoxDict,
    canvas_w: int,
    canvas_h: int,
    *,
    stroke: str,
    stroke_width: int = 2,
    region_name: str = "",
    active: bool = False,
) -> dict[str, Any]:
    left = bbox["x"] / 100.0 * canvas_w
    top = bbox["y"] / 100.0 * canvas_h
    width = bbox["width"] / 100.0 * canvas_w
    height = bbox["height"] / 100.0 * canvas_h
    doc: dict[str, Any] = {
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
    # Keep a stable link between Fabric objects and `area.json` regions.
    # `streamlit-drawable-canvas` may reorder objects, so syncing by list index causes "floating"
    # regions (moving one box updates another region). We store the region name on the object.
    rn = str(region_name or "").strip()
    if rn:
        doc["wos_region_name"] = rn
    if active:
        # Read by our forked streamlit-drawable-canvas after `loadFromJSON` to
        # auto-select the matching Fabric object so resize handles appear
        # without an extra click.
        doc["wos_active"] = True
    return doc


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
        aux = bool(reg.get("overlay_auxiliary"))
        is_selected = i == selected_idx
        if is_selected:
            stroke = "#22c55e"
        elif aux:
            stroke = "#3b82f6"
        else:
            stroke = "#ef4444"
        objects.append(
            _bbox_to_canvas_rect(
                bbox,
                canvas_w,
                canvas_h,
                stroke=stroke,
                region_name=str(reg.get("name") or "").strip(),
                active=is_selected,
            )
        )
    return {"version": CANVAS_VERSION, "objects": objects}


def _origin_offset(value: float, size: float, origin: str) -> float:
    """Convert Fabric origin-based coordinate to top-left coordinate."""
    o = (origin or "left").strip().lower()
    if o == "center":
        return value - size / 2.0
    if o == "right":
        return value - size
    # "left" (and unknown): treat as top-left already
    return value


def _canvas_obj_to_bbox(
    obj: dict[str, Any],
    *,
    canvas_w: int,
    canvas_h: int,
    orig_w: int,
    orig_h: int,
) -> BBoxDict | None:
    """Normalize Fabric rect object into a stable percent bbox.

    `streamlit-drawable-canvas` may emit `originX/originY` other than left/top (e.g. center),
    and different versions may represent scaling via `scaleX/scaleY` and/or baked into `width/height`.
    To avoid feedback loops ("jumping" rectangles), we always convert to **top-left + effective size**
    in canvas coordinates first, then map to percentages.
    """
    if obj.get("type") != "rect":
        return None

    left = float(obj.get("left", 0.0) or 0.0)
    top = float(obj.get("top", 0.0) or 0.0)
    w_raw = float(obj.get("width", 0.0) or 0.0)
    h_raw = float(obj.get("height", 0.0) or 0.0)
    rot = float(obj.get("angle", 0.0) or 0.0)

    # Fabric keeps base width/height + scale for transforms; sometimes scale is already baked.
    sx = float(obj.get("scaleX", 1.0) or 1.0)
    sy = float(obj.get("scaleY", 1.0) or 1.0)

    # Be tolerant to negative scales (flip); we only care about bbox extent.
    eff_w = abs(w_raw * sx)
    eff_h = abs(h_raw * sy)

    origin_x = str(obj.get("originX", "left") or "left")
    origin_y = str(obj.get("originY", "top") or "top")
    left_tl = _origin_offset(left, eff_w, origin_x)
    top_tl = _origin_offset(top, eff_h, origin_y)

    return convert_bbox(
        left_tl,
        top_tl,
        eff_w,
        eff_h,
        canvas_w,
        canvas_h,
        orig_w,
        orig_h,
        rot,
        1.0,
        1.0,
    )


def sync_regions_from_canvas(
    regions: list[RegionDict],
    json_data: dict[str, Any],
    canvas_w: int,
    canvas_h: int,
    orig_w: int,
    orig_h: int,
) -> list[RegionDict]:
    if not json_data or not isinstance(json_data, dict):
        return regions
    new_regions: list[RegionDict] = []
    rect_objs = [o for o in (json_data.get("objects") or []) if isinstance(o, dict)]
    rect_objs = [o for o in rect_objs if o.get("type") == "rect"]
    if not rect_objs:
        return regions

    # Build a stable mapping from canvas objects to regions by name.
    # Fall back to index only if we don't have a name tag.
    by_name: dict[str, RegionDict] = {}
    for r in regions:
        nm = str(r.get("name") or "").strip()
        if nm:
            by_name[nm] = r

    used_names: set[str] = set()
    used_idx: set[int] = set()

    for i, obj in enumerate(rect_objs):
        bbox = _canvas_obj_to_bbox(
            obj,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            orig_w=orig_w,
            orig_h=orig_h,
        )
        if bbox is None:
            continue

        tag = str(obj.get("wos_region_name") or "").strip()
        if tag and tag in by_name and tag not in used_names:
            base = dict(by_name[tag])
            base["bbox"] = bbox
            new_regions.append(base)  # type: ignore[arg-type]
            used_names.add(tag)
            continue

        # Fallback: keep old behavior (index-based) for untaged / freshly drawn rects.
        if i < len(regions) and i not in used_idx:
            base = dict(regions[i])
            base["bbox"] = bbox
            new_regions.append(base)  # type: ignore[arg-type]
            used_idx.add(i)
            continue

        nr = _default_region(orig_w, orig_h)
        nr["name"] = f"region_{i + 1}"
        nr["bbox"] = bbox
        new_regions.append(nr)
    return new_regions


def _regions_bbox_semantic_sig(regions: list[RegionDict]) -> str:
    """Stable signature for region bbox state, independent of Fabric's raw JSON shape."""
    payload: list[list[Any]] = []
    for r in regions:
        bbox = r.get("bbox")
        if not bbox:
            payload.append([str(r.get("name") or "").strip(), None])
            continue
        payload.append(
            [
                str(r.get("name") or "").strip(),
                round(float(bbox.get("x", 0.0) or 0.0), 4),
                round(float(bbox.get("y", 0.0) or 0.0), 4),
                round(float(bbox.get("width", 0.0) or 0.0), 4),
                round(float(bbox.get("height", 0.0) or 0.0), 4),
                round(float(bbox.get("rotation", 0.0) or 0.0), 4),
            ]
        )
    return json.dumps(payload, sort_keys=False, separators=(",", ":"))


def _mirror_canvas_selection_into_session(canvas_result: Any) -> None:
    """Mirror ``canvas_result.active_region_name`` (wos-fork field) into session.

    When the user clicks a rectangle directly on the canvas, the forked
    ``streamlit-drawable-canvas`` reports the selected region's name; this
    helper updates ``selected_region_name`` and reruns so the regions radio
    follows the click. No-op when the field is empty (canvas-side selection
    is unknown) or already in sync with the session.
    """
    if canvas_result is None:
        return
    cr_active = (getattr(canvas_result, "active_region_name", "") or "").strip()
    if not cr_active:
        return
    cur_active = str(st.session_state.get(SELECTED_REGION_NAME) or "").strip()
    if cr_active == cur_active:
        return
    st.session_state.selected_region_name = cr_active
    st.rerun()


def _remember_stale_canvas_sig(sig: str) -> None:
    """Temporarily ignore immediately-returned stale canvas frames after a bbox edit."""
    st.session_state[CANVAS_IGNORE_STALE_BBOX_SIG] = sig
    st.session_state[CANVAS_IGNORE_STALE_UNTIL] = time.time() + 3.0


def _should_ignore_stale_canvas_sig(incoming_sig: str, current_sig: str) -> bool:
    stale_sig = str(st.session_state.get(CANVAS_IGNORE_STALE_BBOX_SIG) or "")
    until = float(st.session_state.get(CANVAS_IGNORE_STALE_UNTIL, 0.0) or 0.0)
    if not stale_sig:
        return False
    if time.time() > until:
        st.session_state.pop(CANVAS_IGNORE_STALE_BBOX_SIG, None)
        st.session_state.pop(CANVAS_IGNORE_STALE_UNTIL, None)
        return False
    return bool(incoming_sig == stale_sig and incoming_sig != current_sig)


def cap_preview_image_max_side(im: Image.Image, max_side: int) -> Image.Image:
    """Return a copy scaled so the longer side is at most ``max_side``."""
    w, h = im.size
    if max(w, h) <= max_side:
        return im.copy()
    out = im.copy()
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return out


def resize_for_canvas(
    im: Image.Image, max_side: int, *, allow_upscale: bool = False
) -> tuple[Image.Image, float]:
    im = im.convert("RGBA")
    w, h = im.size
    ratio = max_side / max(w, h)
    scale = ratio if allow_upscale else min(1.0, ratio)
    if abs(scale - 1.0) > 1e-6:
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    return im, scale


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------


def init_session() -> None:
    if AREA_DOC not in st.session_state:
        try:
            st.session_state.area_doc = load_json(AREA_JSON_PATH)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            st.session_state.area_doc = default_area_doc([])
            st.session_state.load_error = str(e)
    if ENTRY_IDX not in st.session_state:
        screens = st.session_state.area_doc.get("screens") or []
        st.session_state.entry_idx = 0 if screens else -1
    if SELECTED_REGION_IDX not in st.session_state:
        st.session_state.selected_region_idx = 0
    if SELECTED_REGION_NAME not in st.session_state:
        st.session_state.selected_region_name = ""
    if CANVAS_REV not in st.session_state:
        st.session_state.canvas_rev = 0
    if CANVAS_LAST_SIG not in st.session_state:
        st.session_state.last_canvas_sig = ""


def _selected_region_name_from_idx(regions: list[RegionDict], idx: int) -> str:
    if not regions:
        return ""
    if idx < 0 or idx >= len(regions):
        return ""
    return str(regions[idx].get("name") or "").strip()


def _resolve_selected_region_idx(regions: list[RegionDict]) -> int:
    """Return stable selected idx, preferring selection by region name."""
    if not regions:
        st.session_state.selected_region_idx = 0
        st.session_state.selected_region_name = ""
        return 0

    want_name = str(st.session_state.get(SELECTED_REGION_NAME) or "").strip()
    if want_name:
        for i, r in enumerate(regions):
            if str(r.get("name") or "").strip() == want_name:
                st.session_state.selected_region_idx = i
                return i

    idx = int(st.session_state.get(SELECTED_REGION_IDX, 0) or 0)
    idx = max(0, min(idx, len(regions) - 1))
    st.session_state.selected_region_idx = idx
    st.session_state.selected_region_name = _selected_region_name_from_idx(regions, idx)
    return idx


def ensure_entry(entries: list[AreaEntryDict], idx: int) -> None:
    if idx < 0 or idx >= len(entries):
        return
    entry = entries[idx]
    if "regions" not in entry or entry["regions"] is None:
        entry["regions"] = []


def current_regions() -> list[RegionDict]:
    """Return the regions list for the current edit context.

    With no active version, returns the entry's base ``regions[]``. With an
    active version ``vN``, returns ``versions[V].regions[]`` (auto-creating an
    empty list if the version block had no overrides yet, so callers can mutate
    in place). Temporal references stay in session-state only.
    """
    ref = str(st.session_state.get(ANNOT_LABELING_REF) or "")
    if "/temporal/" in ref.replace("\\", "/"):
        v = st.session_state.get(LABELING_TEMPORAL_REGIONS)
        return list(v) if isinstance(v, list) else []
    entries: list[AreaEntryDict] = st.session_state.area_doc["screens"]
    idx: int = st.session_state.entry_idx
    if idx < 0 or idx >= len(entries):
        return []
    ensure_entry(entries, idx)
    cur = entries[idx]
    av = get_active_version(cur)
    if av:
        ver = get_version_block(cur, av)
        if ver is not None:
            regs = ver.get("regions")
            if not isinstance(regs, list):
                regs = []
                ver["regions"] = regs
            return regs  # type: ignore[return-value]
    return cur["regions"]  # type: ignore[return-value]


def _query_param_scalar(name: str) -> str:
    try:
        raw = st.query_params.get(name)
    except Exception:
        return ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").strip()


def _apply_labeling_region_query_selection() -> None:
    """Honor Labeling deep-link ``?region=...`` once for the active entry."""
    wanted = _query_param_scalar("region")
    if not wanted:
        return
    entries: list[AreaEntryDict] = st.session_state.area_doc.get("screens") or []
    entry_idx = int(st.session_state.get(ENTRY_IDX, -1))
    if entry_idx < 0 or entry_idx >= len(entries):
        return
    cur_entry = entries[entry_idx]
    active_version = get_active_version(cur_entry)
    signature = f"{entry_idx}:{active_version or ACTIVE_VERSION_DEFAULT}:{wanted}"
    if st.session_state.get("_labeling_region_query_applied") == signature:
        return

    regions = current_regions()
    for i, reg in enumerate(regions):
        if str(reg.get("name") or "").strip() != wanted:
            continue
        st.session_state.selected_region_idx = i
        st.session_state.selected_region_name = wanted
        st.session_state["_labeling_region_query_applied"] = signature
        return
    st.session_state["_labeling_region_query_applied"] = signature


def set_current_regions(regions: list[RegionDict]) -> None:
    """Write back the regions list for the current edit context (active version or base)."""
    ref = str(st.session_state.get(ANNOT_LABELING_REF) or "")
    if "/temporal/" in ref.replace("\\", "/"):
        st.session_state[LABELING_TEMPORAL_REGIONS] = list(regions)
        return
    entries: list[AreaEntryDict] = st.session_state.area_doc["screens"]
    idx: int = st.session_state.entry_idx
    if idx < 0 or idx >= len(entries):
        return
    cur = entries[idx]
    av = get_active_version(cur)
    if av:
        ver = get_version_block(cur, av)
        if ver is not None:
            ver["regions"] = list(regions)
            return
    cur["regions"] = list(regions)


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

    # Stash the active labeling instance so deeply-nested helpers (like the version-block
    # auto-bind) can locate the rolling live screenshot at `references/temporal/{iid}_current_state.png`.
    if labeling_instance_id:
        st.session_state["labeling_active_instance_id"] = labeling_instance_id

    canvas_max_side = (
        int(labeling_canvas_max_side)
        if labeling_mode and labeling_canvas_max_side is not None
        else (
            LABELING_CANVAS_DISPLAY_MAX_SIDE if labeling_mode else CANVAS_DISPLAY_MAX_SIDE
        )
    )

    if LOAD_ERROR in st.session_state:
        st.warning(f"Could not load area.json: {st.session_state.load_error}")
        del st.session_state.load_error

    if OVL_YAML_WARN in st.session_state:
        st.warning(st.session_state.pop(OVL_YAML_WARN))

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
            prev_ref = st.session_state.get(ANNOT_LABELING_REF)
            if prev_ref != forced_reference_rel:
                st.session_state._annot_labeling_ref = forced_reference_rel
                st.session_state.canvas_rev += 1
                st.session_state.last_canvas_sig = ""
            if not labeling_png_bytes:
                lp_forced = REPO_ROOT / forced_reference_rel
                if lp_forced.is_file():
                    st.session_state.pending_image_path = str(lp_forced.resolve())
        else:
            st.session_state.pop(PIL_ORIGINAL, None)
            st.session_state.pop(ANNOT_LABELING_REF, None)

    if not entries:
        st.session_state.entry_idx = -1
    else:
        ei = st.session_state.entry_idx
        st.session_state.entry_idx = max(0, min(ei, len(entries) - 1))

    entry_idx: int = st.session_state.entry_idx

    if labeling_mode:
        mid_col, right_col = st.columns([1.95, 1.55], gap="small")
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
                        f"id={e.get('id')} [node: "
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
                _render_versions_block(entries, entry_idx)


    # Load image from disk (pending capture or entry OCR path)
    pil_original: Image.Image | None = st.session_state.get(PIL_ORIGINAL)
    image_rel = None
    if entries and 0 <= entry_idx < len(entries):
        image_rel = entries[entry_idx].get("ocr")
        ver_ocr = get_active_version_ocr_override(entries[entry_idx])
        if ver_ocr:
            image_rel = ver_ocr

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
            # Pending captures live under `references/temporal/` and should not create `area.json` entries.
            _is_temporal = "/temporal/" in effective_forced_ref.replace("\\", "/")
            if not _is_temporal:
                ei_new = ensure_entry_for_reference_path(entries, effective_forced_ref)
                st.session_state.entry_idx = ei_new
            prev_ref = st.session_state.get(ANNOT_LABELING_REF)
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
        ver_ocr_override: str | None = None
        if entries and 0 <= entry_idx < len(entries):
            image_rel = entries[entry_idx].get("ocr")
            ver_ocr_override = get_active_version_ocr_override(entries[entry_idx])
            if ver_ocr_override:
                image_rel = ver_ocr_override

        # Detect active-version switch and force a canvas redraw — otherwise streamlit-drawable-canvas
        # keeps the stale background from the previous version's image.
        _prev_ver_path = st.session_state.get("_active_version_image_path")
        if _prev_ver_path != image_rel:
            st.session_state["_active_version_image_path"] = image_rel
            st.session_state.canvas_rev = int(st.session_state.get(CANVAS_REV, 0)) + 1
            st.session_state.last_canvas_sig = ""

        pending = st.session_state.pop(PENDING_IMAGE_PATH, None)
        # Version override always wins over pending/forced refs — pending was set from the
        # URL-driven default file and would otherwise mask the version-specific image.
        if ver_ocr_override:
            pending = None
        if labeling_png_bytes and effective_forced_ref and not ver_ocr_override:
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
        pending = st.session_state.pop(PENDING_IMAGE_PATH, None)
        ver_ocr_override = None
        if entries and 0 <= entry_idx < len(entries):
            ver_ocr_override = get_active_version_ocr_override(entries[entry_idx])
        if ver_ocr_override:
            pending = None
        if labeling_mode and forced_reference_rel and labeling_png_bytes and not ver_ocr_override:
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

    if IMAGE_ERROR in st.session_state:
        st.error(st.session_state.image_error)
        del st.session_state.image_error

    drawing_mode_labeling = "transform"

    # ----- Labeling: canvas left; right column = reference tree + tools + regions + node graph + save -----
    if labeling_mode:
        _apply_labeling_region_query_selection()

        with right_col:
            if pil_original is not None:
                regions_ct = current_regions()
                sel_ct = st.session_state.selected_region_idx
                if regions_ct and sel_ct >= len(regions_ct):
                    st.session_state.selected_region_idx = sel_ct = len(regions_ct) - 1

            _render_regions_expander(pil_original, canvas_max_side, labeling_mode=True)

            if entries and 0 <= entry_idx < len(entries):
                _render_versions_block(entries, entry_idx, show_active_picker=False)

            with st.expander("Screen entry", expanded=False):
                if not effective_forced_ref:
                    st.caption("Choose a PNG in the reference tree above to edit regions.")
                elif effective_forced_ref and entries:
                    ei_cur = entry_idx
                    if 0 <= ei_cur < len(entries):
                        cur_e = entries[ei_cur]
                        st.caption(
                            f"Entry **id={cur_e.get('id')}** · node **"
                            f"{(str(cur_e.get('screen_id', '') or '').strip() or 'None')}**"
                        )
                if entries and 0 <= entry_idx < len(entries):
                    _render_screen_id_and_ocr_fields(doc, entries, entry_idx, labeling_mode=True)


            st.divider()
            if st.button("Save area.json", type="primary", width="stretch", key="save_area_json_lbl"):
                try:
                    removed = save_json(AREA_JSON_PATH, st.session_state.area_doc)
                    msg = f"Wrote {AREA_JSON_PATH}"
                    if removed:
                        msg += f" · removed {removed} redundant version override(s) matching base"
                    st.success(msg)
                    _write_all_region_crops_with_feedback(st.session_state.area_doc)
                    st.session_state.pop(LABELING_PENDING_CAPTURE_REL, None)
                    st.session_state.pop(LABELING_SELECTION_BEFORE_CAPTURE, None)
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

                # current_regions() already returns the active block (base or versions[V].regions),
                # so canvas writes directly into the correct list.
                regions = current_regions()
                sel = _resolve_selected_region_idx(regions)

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
                    key=_canvas_component_key(drawing_mode=drawing_mode_labeling),
                )

                if canvas_result and canvas_result.json_data:
                    sig = json.dumps(canvas_result.json_data, sort_keys=True)
                    if sig != st.session_state.last_canvas_sig:
                        st.session_state.last_canvas_sig = sig
                        prev_sel_name = str(st.session_state.get(SELECTED_REGION_NAME) or "").strip()
                        current_bbox_sig = _regions_bbox_semantic_sig(regions)
                        synced = sync_regions_from_canvas(
                            regions,
                            canvas_result.json_data,
                            canvas_w,
                            canvas_h,
                            orig_w,
                            orig_h,
                        )
                        incoming_bbox_sig = _regions_bbox_semantic_sig(synced)
                        if not _should_ignore_stale_canvas_sig(incoming_bbox_sig, current_bbox_sig):
                            if incoming_bbox_sig != current_bbox_sig:
                                _remember_stale_canvas_sig(current_bbox_sig)
                                set_current_regions(synced)
                            if prev_sel_name:
                                st.session_state.selected_region_name = prev_sel_name
                            _resolve_selected_region_idx(synced)

                _mirror_canvas_selection_into_session(canvas_result)

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
                sel = _resolve_selected_region_idx(regions)
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
                    key=_canvas_component_key(drawing_mode=drawing_mode),
                )

                if canvas_result and canvas_result.json_data:
                    sig = json.dumps(canvas_result.json_data, sort_keys=True)
                    if sig != st.session_state.last_canvas_sig:
                        st.session_state.last_canvas_sig = sig
                        prev_sel_name = str(st.session_state.get(SELECTED_REGION_NAME) or "").strip()
                        current_bbox_sig = _regions_bbox_semantic_sig(regions)
                        synced = sync_regions_from_canvas(
                            regions,
                            canvas_result.json_data,
                            canvas_w,
                            canvas_h,
                            orig_w,
                            orig_h,
                        )
                        incoming_bbox_sig = _regions_bbox_semantic_sig(synced)
                        if not _should_ignore_stale_canvas_sig(incoming_bbox_sig, current_bbox_sig):
                            if incoming_bbox_sig != current_bbox_sig:
                                _remember_stale_canvas_sig(current_bbox_sig)
                                set_current_regions(synced)
                            if prev_sel_name:
                                st.session_state.selected_region_name = prev_sel_name
                            _resolve_selected_region_idx(synced)

                _mirror_canvas_selection_into_session(canvas_result)

                st.caption(
                    "Editing borders: switch to **Move / resize**, click the box, then drag edges or corners. "
                    "**Draw new rectangle** only adds boxes. Regions update automatically when the canvas changes."
                )

        with right_col:
            _render_regions_expander(pil_original, canvas_max_side, labeling_mode=False)

            st.divider()
            if st.button("Save area.json", type="primary", width="stretch", key="save_area_json_std"):
                try:
                    removed = save_json(AREA_JSON_PATH, st.session_state.area_doc)
                    msg = f"Wrote {AREA_JSON_PATH}"
                    if removed:
                        msg += f" · removed {removed} redundant version override(s) matching base"
                    st.success(msg)
                    _write_all_region_crops_with_feedback(st.session_state.area_doc)
                except (OSError, ValueError) as e:
                    st.error(str(e))

            st.caption(f"File: `{AREA_JSON_PATH}`")

    # Footer: raw JSON
    with st.expander("Current JSON (preview)"):
        st.json(st.session_state.area_doc)
