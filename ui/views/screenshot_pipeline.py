"""Documentation: what the worker runs on each rolling ADB capture."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import streamlit as st
import yaml

from analysis.overlay import run_overlay_analysis
from config.loader import load_settings
from layout.area_lookup import screen_region_by_name
from ui.reference_preview import rolling_live_preview_path

_REPO = Path(__file__).resolve().parents[2]
_ANALYZE = _REPO / "references" / "analyze.yaml"
_REFRESH_UI = max(1.0, float(load_settings().worker.device_reference_snapshot_interval_seconds))


def _region_area_action(area_doc: dict[str, Any], region_name: str) -> str:
    pair = screen_region_by_name(area_doc, str(region_name or "").strip())
    if pair is None:
        return ""
    return str(pair[1].get("action") or "")


def _bbox_pct_to_px_rect(bb: dict[str, Any], wi: int, hi: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    w = float(bb.get("width") or 0.0)
    h = float(bb.get("height") or 0.0)
    left = max(0, min(wi - 1, int(x / 100.0 * wi)))
    top = max(0, min(hi - 1, int(y / 100.0 * hi)))
    right = max(left + 1, min(wi, int((x + w) / 100.0 * wi)))
    bottom = max(top + 1, min(hi, int((y + h) / 100.0 * hi)))
    return left, top, right, bottom


def _maybe_downscale_for_ui(image_bgr: np.ndarray, max_side: int = 960) -> np.ndarray:
    hi, wi = image_bgr.shape[:2]
    m = max(hi, wi)
    if m <= max_side:
        return image_bgr
    scale = max_side / float(m)
    return cv2.resize(
        image_bgr,
        (int(round(wi * scale)), int(round(hi * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _annotate_overlay_debug(
    image_bgr: np.ndarray,
    results: dict[str, Any],
    logical_names: list[str],
    area_doc: dict[str, Any],
    rule_search: dict[str, str],
) -> np.ndarray:
    vis = image_bgr.copy()
    hi, wi = vis.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    for logical in logical_names:
        p = results.get(logical)
        if not isinstance(p, dict):
            continue
        sr_nm = str(p.get("search_region") or rule_search.get(logical, "") or "").strip()
        if sr_nm:
            pr = screen_region_by_name(area_doc, sr_nm)
            if pr:
                reg_s = pr[1]
                search_bbox = reg_s.get("bbox")
                if isinstance(search_bbox, dict):
                    L, T, R, B = _bbox_pct_to_px_rect(search_bbox, wi, hi)
                    cv2.rectangle(vis, (L, T), (R, B), (0, 200, 255), 1)

        tl = p.get("top_left")
        tw = int(p.get("template_w") or 0)
        th = int(p.get("template_h") or 0)
        matched = bool(p.get("matched"))
        if isinstance(tl, (list, tuple)) and len(tl) >= 2 and tw > 0 and th > 0:
            x0 = int(float(tl[0]))
            y0 = int(float(tl[1]))
            x1, y1 = min(wi, x0 + tw), min(hi, y0 + th)
            box_col = (0, 220, 0) if matched else (0, 200, 255)
            cv2.rectangle(vis, (x0, y0), (x1, y1), box_col, 2)
            cx, cy = x0 + tw // 2, y0 + th // 2
            cv2.circle(vis, (cx, cy), 5, box_col, 2)
            label = (logical[:28] + (" ✓" if matched else " ✗")) if logical else ""
            if label:
                cv2.putText(
                    vis,
                    label,
                    (x0 + 2, max(18, y0 - 4)),
                    font,
                    0.42,
                    box_col,
                    1,
                    cv2.LINE_AA,
                )

        txp = p.get("tap_x_pct")
        typ = p.get("tap_y_pct")
        if txp is not None and typ is not None:
            tx = int(float(txp) / 100.0 * wi)
            ty = int(float(typ) / 100.0 * hi)
            tx = max(0, min(wi - 1, tx))
            ty = max(0, min(hi - 1, ty))
            cv2.drawMarker(vis, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            cv2.circle(vis, (tx, ty), 9, (0, 0, 255), 2)
    return vis


@st.fragment(run_every=timedelta(seconds=_REFRESH_UI))
def _overlay_live_status_fragment() -> None:
    settings = load_settings()
    insts = settings.instances
    if not insts:
        st.warning("No instances in config — cannot pick a rolling preview path.")
        return

    iids = [i.instance_id for i in insts]
    instance_id = st.selectbox("Instance (rolling PNG)", iids, key="pipeline_overlay_instance")

    only_exist = st.checkbox(
        "Only `exist` regions (from `area.json`) ",
        value=False,
        key="pipeline_overlay_exist_only",
        help="When unchecked, every overlay rule row is shown with its region action.",
    )

    preview_path = rolling_live_preview_path(instance_id)
    if not preview_path.is_file():
        rel = preview_path.relative_to(_REPO)
        st.info(
            f"No rolling file yet: `{rel}` — start the worker or capture from **Instance**."
        )
        return

    image_bgr = cv2.imread(str(preview_path))
    if image_bgr is None:
        st.warning("Could not decode PNG (corrupt or unreadable).")
        return

    area_doc = json.loads((_REPO / "area.json").read_text(encoding="utf-8"))
    results = run_overlay_analysis(image_bgr, repo_root=_REPO)

    rule_order: list[str] = []
    rule_search: dict[str, str] = {}
    rule_tap: dict[str, str] = {}
    if _ANALYZE.is_file():
        raw = yaml.safe_load(_ANALYZE.read_text(encoding="utf-8"))
        ov = raw.get("overlay") if isinstance(raw, dict) else None
        lst = ov if isinstance(ov, list) else []
        for r in lst:
            if not isinstance(r, dict):
                continue
            nm = str(r.get("name") or "").strip()
            if nm:
                rule_order.append(nm)
                sr = r.get("search_region")
                if sr:
                    rule_search[nm] = str(sr).strip()
                tr = r.get("tap_region")
                if tr:
                    rule_tap[nm] = str(tr).strip()

    rows_out: list[dict[str, object]] = []
    visible_logicals: list[str] = []
    for logical in rule_order:
        payload = results.get(logical)
        if not isinstance(payload, dict):
            continue
        region_name = str(payload.get("region") or "").strip()
        area_action = _region_area_action(area_doc, region_name)
        if only_exist and area_action != "exist":
            continue
        visible_logicals.append(logical)

        matched = bool(payload.get("matched"))
        status = "Found" if matched else "Not found"
        score = payload.get("score")
        thr = payload.get("threshold")
        reason = str(payload.get("reason") or "")
        detail = str(payload.get("detail") or "")
        notes_parts = [reason] if reason else []
        if detail and detail != reason:
            notes_parts.append(detail)
        notes = ": ".join(notes_parts)

        sr_disp = payload.get("search_region") or rule_search.get(logical, "")
        tr_disp = payload.get("tap_region") or rule_tap.get(logical, "")
        rows_out.append(
            {
                "overlay_rule": logical,
                "region": region_name,
                "area_action": area_action if area_action else "(unknown)",
                "search_region": sr_disp,
                "tap_region": tr_disp,
                "status": status,
                "score": "" if score is None else round(float(score), 4),
                "threshold": "" if thr is None else thr,
                "notes": notes.strip(),
            }
        )

    with st.expander("Rolling frame: template match vs tap (debug)", expanded=True):
        show_dbg = st.checkbox(
            "Draw match box / search ROI / tap on the rolling PNG",
            value=True,
            key="pipeline_overlay_debug_viz",
        )
        dbg_include_not_found = st.checkbox(
            "Also draw **Not found** rules (below threshold)",
            value=False,
            key="pipeline_overlay_debug_include_not_found",
            help="By default only **Found** overlays are drawn.",
        )
        if show_dbg and visible_logicals:
            only_found = not dbg_include_not_found
            logicals_draw = (
                [
                    ln
                    for ln in visible_logicals
                    if bool(results.get(ln, {}).get("matched"))
                ]
                if only_found
                else visible_logicals
            )
            if only_found and not logicals_draw:
                st.caption(
                    "No **Found** overlays to draw — enable **Also draw Not found…** "
                    "or wait until a rule matches."
                )
            else:
                vis = _annotate_overlay_debug(
                    image_bgr,
                    results,
                    logicals_draw,
                    area_doc,
                    rule_search,
                )
                vis_ui = _maybe_downscale_for_ui(vis)
                st.image(
                    cv2.cvtColor(vis_ui, cv2.COLOR_BGR2RGB),
                    width="stretch",
                )
                st.caption(
                    "**Orange** outline: `search_region` ROI · "
                    "**Green** / **cyan** box: template match "
                    "(green = Found, cyan = below threshold) · "
                    "**Red** cross: tap target (`tap_x_pct` / `tap_y_pct`). "
                    + (
                        "Showing **Found** only (enable the checkbox above for Not found)."
                        if only_found
                        else "Showing **Found** and **Not found**."
                    )
                )
        elif show_dbg:
            st.caption("Nothing to draw — no overlay rows pass the current filter.")

    if not rows_out:
        st.info("No overlay rows to show (check YAML or turn off the `exist` filter).")
        return

    show_table = st.checkbox(
        "Show overlay status table",
        value=True,
        key="pipeline_overlay_show_table",
    )
    if not show_table:
        return

    rows_out.sort(
        key=lambda row: (
            0 if row.get("status") == "Found" else 1,
            str(row.get("overlay_rule") or ""),
        )
    )
    st.caption(
        "Sorted by **status**: **Found** first, **Not found** after; ties use rule name."
    )

    st.dataframe(rows_out, hide_index=True, width="stretch")


st.title("Screenshot pipeline")

settings = load_settings()
wcfg = settings.worker

st.markdown(
    """
Each instance worker periodically grabs the game frame via ADB, overwrites the **rolling preview**
PNG under `references/`, and may run **overlay** template checks on that same frame.

Queue tasks (arena, training, gathering, …) run in a **separate loop** — they are not part of this
per-screenshot pass; they capture or react to the screen inside `task.execute` when they run.
"""
)

st.subheader("Timing and busy flag")
st.markdown(
    f"- **Capture interval:** `{wcfg.device_reference_snapshot_interval_seconds}` s "
    "(`worker.device_reference_snapshot_interval_seconds` in `config/settings.yaml`).\n"
    f"- **Overlay while a task runs:** `{wcfg.overlay_analyze_when_busy}` — when `false`, "
    "`analyze.yaml` matching is **skipped** while a Redis queue task holds the busy flag "
    "(`worker/instance_worker.py`, `_task_busy`)."
)

st.subheader("Steps on each rolling tick")
st.markdown(
    """
1. **ADB screencap** → BGR in memory (`BotActions.capture_screen_bgr`).
2. **Atomic PNG write** → rolling preview path for the instance (**Instance** reads this file).
3. **Overlay** → `run_overlay_analysis` reads the ordered **`overlay`** list in
   `references/analyze.yaml`; for `findIcon`, template from `references/crop/` and regions from
   `area.json` (optional **`search_region`**: sliding `matchTemplate` in a larger ROI).
4. **Queue taps** → matched rules schedule `overlay_tap` (dedup per region).
   Tap: **`tap_offset_from_match`** = match + labeled delta; else **`tap_region`**; else match; else
   template bbox centre.
"""
)

st.subheader("Current overlay rules")
if _ANALYZE.is_file():
    raw = yaml.safe_load(_ANALYZE.read_text(encoding="utf-8"))
    overlay = raw.get("overlay") if isinstance(raw, dict) else None
    rules = overlay if isinstance(overlay, list) else []
    rows_static: list[dict[str, object]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        sr = r.get("search_region")
        tr = r.get("tap_region")
        rows_static.append(
            {
                "name": str(r.get("name", "")),
                "region": str(r.get("region", "")),
                "search_region": str(sr).strip() if sr else "",
                "tap_region": str(tr).strip() if tr else "",
                "action": str(r.get("action", "")),
                "threshold": r.get("threshold"),
            }
        )
    if rows_static:
        st.dataframe(rows_static, hide_index=True, width="stretch")
    else:
        st.info("No entries under `overlay` in analyze.yaml.")
    with st.expander("Raw `references/analyze.yaml`"):
        st.code(_ANALYZE.read_text(encoding="utf-8"), language="yaml")
else:
    st.warning(f"Missing file: `{_ANALYZE}`")

st.subheader("Live overlay status")
st.caption(
    """
Runs **`run_overlay_analysis`** on the rolling PNG (same as worker; worker may skip when busy).
**`area_action`** from **`area.json`**.
For **`exist`**, **Found** means the best OpenCV **TM_CCOEFF_NORMED** score (in the region bbox,
or the **peak inside `search_region`** when set) is **≥** the rule **`threshold`** — not a human
visual check. Large **`search_region`** ROI increases false positives on the wrong screen; raise
**`threshold`** or tighten the ROI in **`area.json`** if needed.
""".strip()
)
_overlay_live_status_fragment()

st.divider()
st.subheader("YAML scenarios under `scenarios/` (not every screenshot)")
st.markdown(
    """
Files in `scenarios/*.yaml` describe **macro** routines (daily routine, arena rush, …).

The **scheduler** uses its own interval: it expands YAML steps into queue tasks; the worker runs
those tasks **between** rolling preview ticks.

Enable flags and per-player assignment: sidebar **Scenarios**.
"""
)
sch = settings.scheduler
st.caption(f"Scheduler tick interval in settings: `{sch.interval_seconds}` s.")
