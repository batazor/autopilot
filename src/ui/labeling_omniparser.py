"""OmniParser auto-label controls for the labeling page."""

from __future__ import annotations

from typing import Any

import streamlit as st
from PIL import Image

from omniparser.client import (
    check_omniparser_health,
    parse_screenshot,
    resolve_omniparser_local_backend,
    resolve_omniparser_mode,
    resolve_omniparser_url,
)
from omniparser.supervision_bridge import (
    OMNIPARSER_CROP_HASH_BLACKLIST,
    OMNIPARSER_NAME_BLACKLIST_PREFIXES,
    build_omniparser_proposal_regions,
    merge_detected_regions,
    merge_omniparser_regions,
    parsed_element_to_dict,
    reuse_proposal_names_from_existing_crops,
    reuse_proposal_names_from_overlapping_regions,
)
from ui.area_annotator import (
    PIL_ORIGINAL,
    _write_all_region_crops_with_feedback,
    current_regions,
    save_json,
    set_current_regions,
)

OMNIPARSER_LABELING_SESSION = "omniparser_labeling_proposal"


def _related_regions_for_current_screen() -> list[dict[str, Any]]:
    doc = st.session_state.get("area_doc")
    if not isinstance(doc, dict):
        return []
    entries = doc.get("screens")
    if not isinstance(entries, list):
        return []
    idx = int(st.session_state.get("entry_idx", -1))
    if idx < 0 or idx >= len(entries):
        return []
    cur_entry = entries[idx]
    if not isinstance(cur_entry, dict):
        return []
    screen_id = str(cur_entry.get("screen_id") or "").strip()
    if not screen_id:
        return []

    current_ids = {id(r) for r in current_regions() if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("screen_id") or "").strip() != screen_id:
            continue
        for reg in entry.get("regions") or []:
            if isinstance(reg, dict) and id(reg) not in current_ids:
                out.append(reg)
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            for reg in ver.get("regions") or []:
                if isinstance(reg, dict) and id(reg) not in current_ids:
                    out.append(reg)
    return out


def _filter_blacklisted_omniparser_regions(
    image: Image.Image,
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    from omniparser.supervision_bridge import filter_blacklisted_regions

    return filter_blacklisted_regions(image, regions)


def render_omniparser_labeling_controls(*, labeling_mode: bool) -> None:
    """Toolbar inside the Regions expander (labeling only)."""

    if not labeling_mode:
        return
    mode = resolve_omniparser_mode()
    url = resolve_omniparser_url() if mode == "http" else ""
    proposal = st.session_state.get(OMNIPARSER_LABELING_SESSION)

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
            "Apply mode (stored until you Apply)",
            options=["merge", "replace"],
            format_func=lambda m: "Merge new names" if m == "merge" else "Replace all regions",
            horizontal=True,
        )
        use_paddle = st.checkbox("Use PaddleOCR (upstream default)", value=True)

        run_cols = st.columns(2)
        with run_cols[0]:
            if st.button("Run OmniParser on this screenshot", type="primary", width="stretch"):
                pil_run: Image.Image | None = st.session_state.get(PIL_ORIGINAL)
                if pil_run is None:
                    st.error("No screenshot loaded on the canvas.")
                    return
                with st.spinner("OmniParser is parsing the screen… (first run loads models)"):
                    try:
                        parsed = parse_screenshot(
                            pil_run,
                            url=url or None,
                            box_threshold=box_threshold,
                            iou_threshold=iou_threshold,
                            use_paddleocr=use_paddle,
                        )
                    except Exception as exc:
                        st.error(str(exc))
                        return
                    proposal_regions, stats = build_omniparser_proposal_regions(
                        parsed.elements,
                        pil_run,
                        width=parsed.width,
                        height=parsed.height,
                        min_area_pct=float(min_area),
                        nms_iou_threshold=float(iou_threshold),
                    )
                    proposal_regions, crop_name_reused = reuse_proposal_names_from_existing_crops(
                        pil_run,
                        proposal_regions,
                        current_regions(),  # ty: ignore[invalid-argument-type]
                    )
                    (
                        proposal_regions,
                        current_overlap_name_reused,
                        current_overlap_duplicate_dropped,
                    ) = reuse_proposal_names_from_overlapping_regions(
                        proposal_regions,
                        current_regions(),  # ty: ignore[invalid-argument-type]
                    )
                    (
                        proposal_regions,
                        sibling_overlap_name_reused,
                        sibling_overlap_duplicate_dropped,
                    ) = reuse_proposal_names_from_overlapping_regions(
                        proposal_regions,
                        _related_regions_for_current_screen(),
                    )
                    overlap_name_reused = current_overlap_name_reused + sibling_overlap_name_reused
                    overlap_duplicate_dropped = (
                        current_overlap_duplicate_dropped + sibling_overlap_duplicate_dropped
                    )
                st.session_state[OMNIPARSER_LABELING_SESSION] = {
                    "elements": [parsed_element_to_dict(el) for el in parsed.elements],
                    "width": int(parsed.width),
                    "height": int(parsed.height),
                    "params": {
                        "box_threshold": float(box_threshold),
                        "iou_threshold": float(iou_threshold),
                        "min_area_pct": float(min_area),
                        "nms_iou_threshold": float(iou_threshold),
                        "use_paddleocr": bool(use_paddle),
                    },
                    "merge_mode": merge_mode,
                    "proposal_regions": proposal_regions,
                    "stats": {
                        "raw_element_count": stats.raw_element_count,
                        "skipped_min_area": stats.skipped_min_area,
                        "after_min_area_count": stats.after_min_area_count,
                        "after_nms_count": stats.after_nms_count,
                        "nms_removed": stats.nms_removed,
                        "blacklist_skipped": stats.blacklist_skipped,
                        "crop_hash_name_reused": int(crop_name_reused),
                        "current_overlap_name_reused": int(current_overlap_name_reused),
                        "current_overlap_duplicate_dropped": int(current_overlap_duplicate_dropped),
                        "sibling_overlap_name_reused": int(sibling_overlap_name_reused),
                        "sibling_overlap_duplicate_dropped": int(sibling_overlap_duplicate_dropped),
                        "overlap_name_reused": int(overlap_name_reused),
                        "overlap_duplicate_dropped": int(overlap_duplicate_dropped),
                    },
                }
                summ = (
                    f"Detected **{stats.raw_element_count}** raw element(s) → "
                    f"**{len(proposal_regions)}** region proposal(s). "
                    f"NMS −{stats.nms_removed}; blacklist −{stats.blacklist_skipped}."
                )
                if crop_name_reused:
                    summ += f" Reused **{crop_name_reused}** name(s) from current regions (overlap + crop hash)."
                if current_overlap_name_reused:
                    summ += (
                        f" Reused **{current_overlap_name_reused}** name(s) "
                        "from current canvas regions (80% overlap)."
                    )
                if sibling_overlap_name_reused:
                    summ += (
                        f" Reused **{sibling_overlap_name_reused}** name(s) "
                        "from sibling screen regions (80% overlap)."
                    )
                if overlap_duplicate_dropped:
                    summ += f" Dropped **{overlap_duplicate_dropped}** duplicate overlap proposal(s)."
                summ += " Use **Apply proposal** below to update regions."
                st.success(summ)
                st.rerun()

        with run_cols[1]:
            if proposal and st.button("Discard proposal", width="stretch"):
                st.session_state.pop(OMNIPARSER_LABELING_SESSION, None)
                st.success("Omni proposal cleared.")
                st.rerun()

        if proposal:
            regs = proposal.get("proposal_regions")
            nr = len(regs) if isinstance(regs, list) else 0
            st.info(
                f"Pending Omni proposal · **{nr}** region(s) · mode **"
                f"{proposal.get('merge_mode', '?')}**. Apply uses current canvas regions."
            )
            stats_p = proposal.get("stats") or {}
            st.caption(
                "Parse stats: raw "
                f"{stats_p.get('raw_element_count')} · skipped min-area "
                f"{stats_p.get('skipped_min_area')} · after NMS "
                f"{stats_p.get('after_nms_count')} · crop-hash reuse "
                f"{stats_p.get('crop_hash_name_reused', 0)} · overlap reuse "
                f"{stats_p.get('overlap_name_reused', 0)}."
            )
            save_after = st.checkbox("Save area.json after apply", value=False)

            apply_clicked = st.button("Apply proposal to regions", type="secondary", width="stretch")

            if apply_clicked:
                preg = proposal.get("proposal_regions")
                if not isinstance(preg, list):
                    st.error("Stored proposal has no regions — run OmniParser again.")
                    return
                mode_apply = str(proposal.get("merge_mode") or "merge")
                existing = current_regions()
                merged, added, aliased, skipped_ix = merge_detected_regions(
                    merge_mode=mode_apply,
                    existing=existing,  # ty: ignore[invalid-argument-type]
                    proposed_regions=preg,
                )
                set_current_regions(merged)  # ty: ignore[invalid-argument-type]
                st.session_state.canvas_rev = int(st.session_state.get("canvas_rev", 0)) + 1
                applied_msg = (
                    f"Applied ({mode_apply}): **{added}** region(s) added / replaced; "
                    f"**{aliased}** alias(es); **{skipped_ix}** overlap skip(s)."
                )
                if save_after:
                    try:
                        from ui.keys import LABELING_AREA_DIRTY
                        from ui.wiki_module import active_wiki_area_path

                        area_path = active_wiki_area_path()
                        removed_s = save_json(area_path, st.session_state.area_doc)
                        crop_msg = f" Wrote `{area_path}`"
                        if removed_s:
                            crop_msg += f" · removed {removed_s} redundant version override(s)."
                        _write_all_region_crops_with_feedback(st.session_state.area_doc)
                        st.session_state[LABELING_AREA_DIRTY] = False
                        applied_msg += crop_msg
                    except (OSError, ValueError) as exc:
                        applied_msg += f" (save failed: {exc})"

                st.session_state.pop(OMNIPARSER_LABELING_SESSION, None)
                st.success(applied_msg)
                st.rerun()


# Re-export for tests and callers that patched these on ``ui.labeling_omniparser``.
__all__ = [
    "OMNIPARSER_CROP_HASH_BLACKLIST",
    "OMNIPARSER_NAME_BLACKLIST_PREFIXES",
    "_filter_blacklisted_omniparser_regions",
    "merge_omniparser_regions",
    "render_omniparser_labeling_controls",
]

