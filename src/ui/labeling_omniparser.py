"""OmniParser auto-label controls for the labeling page."""
from __future__ import annotations

import hashlib
import math

import streamlit as st
from PIL import Image

from omniparser.client import (
    check_omniparser_health,
    parse_screenshot,
    resolve_omniparser_local_backend,
    resolve_omniparser_mode,
    resolve_omniparser_url,
)
from omniparser.convert import elements_to_regions
from omniparser.convert import region_hash
from ui.area_annotator import (
    PIL_ORIGINAL,
    current_regions,
    set_current_regions,
)

OMNIPARSER_CROP_HASH_BLACKLIST: frozenset[str] = frozenset(
    {
        # `icon.unanswerable*` crops from welcome_back.png. OmniParser labels
        # these as icons, but they are decorative popup artwork.
        "3bd39f05ac16b1ba678908b8240853d1f3346051ad89782fb1fc10a817f162c2",
        "25d8c3d35b3e3f7985656beecadd4ebbebbe58df7706ea1890ab66a3c53de57d",
        "f662a6201f1fa74f8236d2f55999856bcf6c95d3d5f120b6ef03819a07fc9dc7",
        "f5d4abad731b372f880b0a0e2b5a6688a9e6729340e6e39ed8d172c1b55fee0c",
    }
)
OMNIPARSER_NAME_BLACKLIST_PREFIXES: tuple[str, ...] = ("icon.unanswerable",)


def _bbox_rect(region: dict[str, object]) -> tuple[float, float, float, float] | None:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w = float(bbox.get("width", 0.0))
        h = float(bbox.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h)


def _rects_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _has_bbox_intersection(region: dict[str, object], existing_rects: list[tuple[float, float, float, float]]) -> bool:
    rect = _bbox_rect(region)
    if rect is None:
        return False
    return any(_rects_intersect(rect, existing) for existing in existing_rects)


def _region_names(region: dict[str, object]) -> list[str]:
    out: list[str] = []
    name = str(region.get("name") or "").strip()
    if name:
        out.append(name)
    aliases = region.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            alias_s = str(alias or "").strip()
            if alias_s and alias_s not in out:
                out.append(alias_s)
    return out


def _region_identity_hashes(region: dict[str, object]) -> set[str]:
    h = str(region.get("hash") or "").strip()
    hashes = {region_hash(region)}
    if h:
        hashes.add(h)
    return hashes


def _region_crop_pixel_hash(image: Image.Image, region: dict[str, object]) -> str | None:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w_pct = float(bbox.get("width", 0.0))
        h_pct = float(bbox.get("height", 0.0))
    except (TypeError, ValueError):
        return None
    if w_pct <= 0 or h_pct <= 0:
        return None

    image_rgba = image.convert("RGBA")
    ow, oh = image_rgba.size
    left = x / 100.0 * ow
    top = y / 100.0 * oh
    width = w_pct / 100.0 * ow
    height = h_pct / 100.0 * oh
    l_px = max(0, min(math.floor(left), ow - 1))
    t_px = max(0, min(math.floor(top), oh - 1))
    r_px = max(l_px + 1, min(math.ceil(left + width), ow))
    b_px = max(t_px + 1, min(math.ceil(top + height), oh))
    crop = image_rgba.crop((l_px, t_px, r_px, b_px))
    return hashlib.sha256(crop.tobytes()).hexdigest()


def _is_blacklisted_omniparser_region(image: Image.Image, region: dict[str, object]) -> bool:
    name = str(region.get("name") or "").strip().lower()
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in OMNIPARSER_NAME_BLACKLIST_PREFIXES):
        return True
    crop_hash = _region_crop_pixel_hash(image, region)
    return bool(crop_hash and crop_hash in OMNIPARSER_CROP_HASH_BLACKLIST)


def _filter_blacklisted_omniparser_regions(
    image: Image.Image,
    regions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    kept: list[dict[str, object]] = []
    skipped = 0
    for region in regions:
        if _is_blacklisted_omniparser_region(image, region):
            skipped += 1
            continue
        kept.append(region)
    return kept, skipped


def _add_region_alias(region: dict[str, object], alias: str, taken_names: set[str]) -> bool:
    alias_s = alias.strip()
    if not alias_s or alias_s in _region_names(region) or alias_s in taken_names:
        return False
    aliases = region.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
        region["aliases"] = aliases
    aliases.append(alias_s)
    taken_names.add(alias_s)
    return True


def merge_omniparser_regions(
    existing: list[dict[str, object]],
    proposed: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int, int, int]:
    """Merge proposed OmniParser regions into the current screen only.

    Returns ``(merged, added, aliased, skipped_intersections)``. If a proposed
    region has the same identity hash as an existing current-screen region, the
    proposed name becomes an alias instead of creating a duplicate bbox.
    """
    merged = list(existing)
    names = {name for region in merged for name in _region_names(region)}
    by_hash: dict[str, dict[str, object]] = {}
    for region in merged:
        for h in _region_identity_hashes(region):
            by_hash.setdefault(h, region)

    existing_rects = [
        rect
        for region in merged
        if (rect := _bbox_rect(region)) is not None
    ]
    added = 0
    aliased = 0
    skipped_intersections = 0
    for reg in proposed:
        nm = str(reg.get("name") or "").strip()
        matched_region = next(
            (by_hash[h] for h in _region_identity_hashes(reg) if h in by_hash),
            None,
        )
        if matched_region is not None:
            if _add_region_alias(matched_region, nm, names):
                aliased += 1
            continue
        if nm in names:
            continue
        if _has_bbox_intersection(reg, existing_rects):
            skipped_intersections += 1
            continue
        merged.append(reg)
        for name in _region_names(reg):
            names.add(name)
        for h in _region_identity_hashes(reg):
            by_hash.setdefault(h, reg)
        if (rect := _bbox_rect(reg)) is not None:
            existing_rects.append(rect)
        added += 1
    return merged, added, aliased, skipped_intersections


def render_omniparser_labeling_controls(*, labeling_mode: bool) -> None:
    """Toolbar inside the Regions expander (labeling only)."""

    if not labeling_mode:
        return
    mode = resolve_omniparser_mode()
    url = resolve_omniparser_url() if mode == "http" else ""
    with st.expander("Auto-label (OmniParser)", expanded=False):
        if mode == "local":
            backend = resolve_omniparser_local_backend()
            st.caption(
                "Runs [OmniParser](https://github.com/microsoft/OmniParser) in a one-shot local "
                f"subprocess for the current screenshot (`{backend}` backend); "
                "the process exits after parsing."
            )
        else:
            st.caption(
                "Calls a local [OmniParser](https://github.com/microsoft/OmniParser) sidecar "
                "(`omniparser.service`) to propose bounding boxes for the current screenshot."
            )
        if mode == "http" and not url:
            st.warning(
                "Set `omniparser.url` in `config/settings.yaml` or `OMNIPARSER_URL` in `.env` "
                "(e.g. `http://127.0.0.1:8765`), then start the sidecar."
            )
            return
        health = check_omniparser_health(url=url)
        if health.get("ok"):
            if mode == "local":
                st.caption("Local one-shot backend ready; models load inside each subprocess run.")
            else:
                loaded = "models loaded" if health.get("models_loaded") else "models not loaded yet"
                st.caption(f"Sidecar **{url}** · {loaded}")
        else:
            err = health.get("error", health)
            if mode == "local":
                st.error(f"OmniParser local backend is not ready: {err}")
            elif health.get("omniparser_root") is not None:
                st.error(f"OmniParser sidecar is running but not ready at `{url}`: {err}")
            else:
                st.error(f"Cannot reach OmniParser at `{url}`: {err}")

        c1, c2, c3 = st.columns(3)
        with c1:
            box_threshold = st.slider("Box threshold", 0.01, 0.5, 0.05, 0.01)
        with c2:
            iou_threshold = st.slider("IOU threshold", 0.01, 0.5, 0.1, 0.01)
        with c3:
            min_area = st.slider("Min area %", 0.01, 2.0, 0.04, 0.01)

        merge_mode = st.radio(
            "Apply mode",
            options=["merge", "replace"],
            format_func=lambda m: "Merge new names" if m == "merge" else "Replace all regions",
            horizontal=True,
        )
        use_paddle = st.checkbox("Use PaddleOCR (upstream default)", value=True)

        if st.button("Run OmniParser on this screenshot", type="primary", width="stretch"):
            pil: Image.Image | None = st.session_state.get(PIL_ORIGINAL)
            if pil is None:
                st.error("No screenshot loaded on the canvas.")
                return
            with st.spinner("OmniParser is parsing the screen… (first run loads models)"):
                try:
                    parsed = parse_screenshot(
                        pil,
                        url=url or None,
                        box_threshold=box_threshold,
                        iou_threshold=iou_threshold,
                        use_paddleocr=use_paddle,
                    )
                except Exception as exc:
                    st.error(str(exc))
                    return
            existing = current_regions()
            proposed = elements_to_regions(
                list(parsed.elements),
                image_width=parsed.width,
                image_height=parsed.height,
                min_area_pct=float(min_area),
            )
            proposed, skipped_blacklisted = _filter_blacklisted_omniparser_regions(
                pil,
                proposed,
            )
            skipped_intersections = 0
            aliased = 0
            if merge_mode == "replace":
                set_current_regions(proposed)
                added = len(proposed)
            else:
                merged, added, aliased, skipped_intersections = merge_omniparser_regions(
                    existing,
                    proposed,
                )
                set_current_regions(merged)
            st.session_state.canvas_rev = int(st.session_state.get("canvas_rev", 0)) + 1
            msg = (
                f"OmniParser: **{len(parsed.elements)}** elements detected, "
                f"**{added}** region(s) added ({merge_mode}). Save `area.json` when ready."
            )
            if skipped_blacklisted:
                msg += f" Skipped **{skipped_blacklisted}** blacklisted decorative region(s)."
            if merge_mode != "replace" and skipped_intersections:
                msg += f" Skipped **{skipped_intersections}** overlapping region(s)."
            if merge_mode != "replace" and aliased:
                msg += f" Added **{aliased}** alias(es) for matching hash(es)."
            st.success(msg)
            st.rerun()
