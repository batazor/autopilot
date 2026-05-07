"""Wiki: browse scenario YAML as a human-readable story."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
import yaml
from PIL import Image, ImageDraw, ImageFont
from streamlit_extras.stoggle import stoggle


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

WIKI_STORY_DISPLAY_MAX_SIDE = 600


def _camel_to_snake(s: str) -> str:
    # Best-effort: allow searching for "is_new_people" while filename is "isNewPeople".
    if not s:
        return s
    out: list[str] = []
    prev_is_lower = False
    for ch in s:
        is_upper = "A" <= ch <= "Z"
        if is_upper and prev_is_lower:
            out.append("_")
        out.append(ch.lower())
        prev_is_lower = ("a" <= ch <= "z") or ("0" <= ch <= "9")
    return "".join(out)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _abs_image_path(repo_root: Path, rel: str) -> Path | None:
    rel = (rel or "").strip()
    if not rel:
        return None
    p = (repo_root / rel).resolve()
    return p if p.is_file() else None


def _crop_image_path(repo_root: Path, *, ocr_rel: str, region_name: str) -> Path | None:
    """
    Convention: references/crop/<ocr_stem>_<region_name>.png
    Example: references/isNewPeople.welcome_in.png + region welcome_in
      -> references/crop/isNewPeople.welcome_in_welcome_in.png
    """
    ocr_rel = (ocr_rel or "").strip()
    region_name = (region_name or "").strip()
    if not ocr_rel or not region_name:
        return None
    ocr_stem = Path(ocr_rel).stem
    p = (repo_root / "references" / "crop" / f"{ocr_stem}_{region_name}.png").resolve()
    return p if p.is_file() else None


def _open_image(path: Path) -> Image.Image | None:
    try:
        im = Image.open(path).convert("RGBA")
        im.load()
        return im
    except OSError:
        return None


def _resize_for_display(im: Image.Image, *, max_side: int) -> Image.Image:
    """Scale image so longer side == max_side (up or down)."""
    w, h = im.size
    if w <= 0 or h <= 0:
        return im
    scale = max_side / max(w, h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) == (w, h):
        return im
    return im.resize((nw, nh), Image.Resampling.LANCZOS)


def _ref_under_references(ocr_rel: str) -> str | None:
    """Convert `references/foo.png` -> `foo.png` for Labeling deep-link."""
    s = (ocr_rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return None
    if s.startswith("references/"):
        s = s.removeprefix("references/")
    return s or None


def _bbox_px(bbox: dict[str, Any], *, w: int, h: int) -> tuple[int, int, int, int] | None:
    try:
        x = float(bbox["x"])
        y = float(bbox["y"])
        bw = float(bbox["width"])
        bh = float(bbox["height"])
    except Exception:
        return None
    left = int(round(x / 100.0 * w))
    top = int(round(y / 100.0 * h))
    right = int(round((x + bw) / 100.0 * w))
    bottom = int(round((y + bh) / 100.0 * h))
    left = max(0, min(left, w - 1))
    top = max(0, min(top, h - 1))
    right = max(left + 1, min(right, w))
    bottom = max(top + 1, min(bottom, h))
    return left, top, right, bottom


def _annotate_regions(
    base: Image.Image,
    *,
    regions: list[tuple[str, dict[str, Any] | None]],
    active: str | None = None,
) -> Image.Image:
    """Return a copy of base with region bboxes drawn."""
    im = base.copy()
    draw = ImageDraw.Draw(im)
    W, H = im.size
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    for name, bbox in regions:
        if not bbox:
            continue
        rect = _bbox_px(bbox, w=W, h=H)
        if rect is None:
            continue
        l, t, r, b = rect
        is_active = active is not None and name == active
        stroke = (34, 197, 94, 255) if is_active else (239, 68, 68, 255)  # green vs red
        draw.rectangle([l, t, r, b], outline=stroke, width=4 if is_active else 3)
        label = name
        tx, ty = l + 6, max(0, t - 18)
        draw.rectangle([tx - 2, ty - 2, tx + 8 * max(6, len(label)) + 2, ty + 14], fill=(0, 0, 0, 140))
        draw.text((tx, ty), label, fill=(255, 255, 255, 230), font=font)
    return im


@dataclass(frozen=True)
class RegionRef:
    name: str
    screen_id: str
    ocr: str
    bbox: dict[str, Any] | None


def _index_regions(area_doc: dict[str, Any]) -> dict[str, RegionRef]:
    out: dict[str, RegionRef] = {}
    for scr in (area_doc.get("screens") or []) if isinstance(area_doc, dict) else []:
        if not isinstance(scr, dict):
            continue
        screen_id = str(scr.get("screen_id") or "")
        ocr = str(scr.get("ocr") or "")
        for reg in scr.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            nm = str(reg.get("name") or "").strip()
            if not nm:
                continue
            bbox = reg.get("bbox")
            out[nm] = RegionRef(name=nm, screen_id=screen_id, ocr=ocr, bbox=bbox if isinstance(bbox, dict) else None)
    return out


def _collect_step_regions(steps: Any) -> set[str]:
    used: set[str] = set()
    if not isinstance(steps, list):
        return used
    for step in steps:
        if not isinstance(step, dict):
            continue
        # Imperative DSL style
        if "click" in step:
            reg = str(step.get("click") or "").strip()
            if reg:
                used.add(reg)
        # Some steps may refer to regions in nested params; keep it conservative for now.
    return used


def _scenario_pages(*, used_regions: list[str], regions_idx: dict[str, RegionRef]) -> list[str]:
    pages: set[str] = set()
    for nm in used_regions:
        ref = regions_idx.get(nm)
        if ref and ref.screen_id:
            pages.add(ref.screen_id)
    return sorted(pages)


def _primary_page(pages: list[str]) -> str:
    if not pages:
        return "(unknown)"
    if len(pages) == 1:
        return pages[0]
    return f"(multi: {', '.join(pages)})"


def _render_story_steps(*, steps: Any, repo_root: Path, regions_idx: dict[str, RegionRef]) -> None:
    if not isinstance(steps, list) or not steps:
        st.info("No `steps`.")
        return

    for idx, step in enumerate(steps, start=1):
        if isinstance(step, str):
            st.markdown(f"**{idx}.** {step}")
            continue
        if not isinstance(step, dict):
            st.markdown(f"**{idx}.** (unsupported step: {type(step).__name__})")
            continue

        # Imperative DSL scenario (click/wait)
        if "click" in step:
            reg = str(step.get("click") or "").strip()
            st.markdown(f"**{idx}.** Click `{reg}`" if reg else f"**{idx}.** Click (missing region)")
            if reg:
                ref = regions_idx.get(reg)
                if ref is not None:
                    full = _abs_image_path(repo_root, ref.ocr)
                    if full is not None:
                        im = _open_image(full)
                        if im is not None:
                            annotated = _annotate_regions(im, regions=[(reg, ref.bbox)], active=reg)
                            shown = _resize_for_display(annotated, max_side=WIKI_STORY_DISPLAY_MAX_SIDE)
                            ref_under = _ref_under_references(ref.ocr)
                            c_img, c_btn = st.columns([4, 1], vertical_alignment="top")
                            with c_img:
                                st.image(shown, caption=f"{reg} on {ref.ocr}")
                            with c_btn:
                                if ref_under:
                                    k = f"wiki_scenarios_to_labeling_{idx}_{reg}_{ref_under}"
                                    if st.button("Open in Labeling", key=k, use_container_width=True):
                                        try:
                                            st.query_params["ref"] = ref_under
                                        except Exception:
                                            pass
                                        try:
                                            st.switch_page("ui/views/labeling.py")
                                        except Exception:
                                            # Fallback: user can manually open Wiki → Labeling; query param is set above.
                                            st.info("Open **Wiki → Labeling** to view this reference.")
            continue
        if "wait" in step:
            w = step.get("wait")
            st.markdown(f"**{idx}.** Wait `{w}`")
            continue

        # Scheduler-style scenario (task steps)
        if "task" in step:
            tid = str(step.get("id") or "").strip()
            task = str(step.get("task") or "").strip()
            pr = step.get("priority")
            cd = step.get("cooldown")
            extra: list[str] = []
            if pr is not None:
                extra.append(f"prio={pr}")
            if cd is not None:
                extra.append(f"cooldown={cd}")
            tail = f" ({', '.join(extra)})" if extra else ""
            prefix = f"**{idx}.**"
            if tid:
                prefix += f" `{tid}`"
            st.markdown(f"{prefix} Run task `{task}`{tail}")
            params = step.get("params")
            cond = step.get("conditions")
            if isinstance(params, dict) and params:
                stoggle(
                    "Params",
                    yaml.safe_dump(params, allow_unicode=True, sort_keys=False).strip(),
                )
            if isinstance(cond, list) and cond:
                stoggle(
                    "Conditions",
                    yaml.safe_dump(cond, allow_unicode=True, sort_keys=False).strip(),
                )
            continue

        # Fallback for unknown step shapes
        st.markdown(f"**{idx}.**")
        st.code(yaml.safe_dump(step, allow_unicode=True, sort_keys=False).strip(), language="yaml")


st.title("Wiki · Scenarios")
st.caption("Browse scenario YAML files as a readable story (what it clicks, waits, and runs).")

repo_root = _repo_root()
scenarios_dir = repo_root / "scenarios"
area_path = repo_root / "area.json"

files = sorted(scenarios_dir.rglob("*.yaml")) if scenarios_dir.is_dir() else []
if not files:
    st.warning(f"No scenario YAML found under `{scenarios_dir}`.")
    st.stop()

area_doc = _load_yaml(area_path) if area_path.is_file() else {}
regions_idx = _index_regions(area_doc)

params = st.query_params
q_param = params.get("q")
q_default = q_param if isinstance(q_param, str) else ""
show_all = params.get("show_all")
show_all_flag = (str(show_all).strip() == "1") if show_all is not None else False

q = st.text_input("Filter (name/path/key contains)", value=q_default, key="wiki_scenarios_filter").strip().lower()
show_enabled_only = st.checkbox("Only enabled=true", value=not show_all_flag)

items: list[tuple[Path, dict[str, Any]]] = []
for p in files:
    doc = _load_yaml(p)
    if not doc:
        continue
    skey = p.stem
    skey_snake = _camel_to_snake(skey)
    name = str(doc.get("name") or "")
    enabled = doc.get("enabled")
    hay = f"{skey}\n{skey_snake}\n{name}\n{p.relative_to(repo_root).as_posix()}".lower()
    if q and q not in hay:
        continue
    if show_enabled_only and bool(enabled) is not True:
        continue
    items.append((p, doc))

indexed: list[dict[str, Any]] = []
for p, doc in items:
    steps = doc.get("steps")
    used_regions = sorted(_collect_step_regions(steps))
    pages = _scenario_pages(used_regions=used_regions, regions_idx=regions_idx)
    indexed.append(
        {
            "path": p,
            "doc": doc,
            "used_regions": used_regions,
            "pages": pages,
        }
    )

st.subheader(f"Found {len(indexed)} scenario file(s)")

for it in indexed:
    p: Path = it["path"]
    doc: dict[str, Any] = it["doc"]
    used_regions: list[str] = it["used_regions"]
    pages: list[str] = it["pages"]

    skey = p.stem
    name = str(doc.get("name") or skey)
    enabled = doc.get("enabled", None)
    priority = doc.get("priority", None)
    steps = doc.get("steps")

    label = f"{name}  ·  `{skey}`"
    with st.expander(label, expanded=False):
        rel = p.relative_to(repo_root).as_posix()
        meta_cols = st.columns([2.2, 1.2, 1.2, 3.4])
        meta_cols[0].markdown(f"**File**: `{rel}`")
        meta_cols[1].markdown(f"**Enabled**: `{enabled}`")
        meta_cols[2].markdown(f"**Priority**: `{priority}`")
        page_str = ", ".join(pages) if pages else "(unknown)"
        meta_cols[3].markdown(f"**Page(s)**: `{page_str}`")
        st.caption(f"Steps: {len(steps) if isinstance(steps, list) else 0}")

        st.divider()
        st.markdown("**Story**")
        _render_story_steps(steps=steps, repo_root=repo_root, regions_idx=regions_idx)

