"""Documentation: what the worker runs on each rolling ADB capture."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import streamlit as st
import yaml

from analysis.overlay import load_analyze_yaml
from config.loader import load_settings
from ui.keys import PIPELINE_OVERLAY_CACHE
from ui.pipeline.data import force_nonce, get_or_build_pipeline_cache
from ui.pipeline.debug_flow import debug_flow_mode_fragment
from ui.pipeline.overlay_viz import (
    annotate_overlay_debug,
    maybe_downscale_for_ui,
    region_area_action,
)
from ui.reference_preview import rolling_live_preview_path
from ui.redis_client import get_instance_state, require_redis_connection

_REPO = Path(__file__).resolve().parents[2]
_ANALYZE = _REPO / "references" / "analyze.yaml"
_AREA = _REPO / "area.json"
_REFRESH_UI = max(1.0, float(load_settings().worker.device_reference_snapshot_interval_seconds))

# ---------------------------------------------------------------------------
# Live status fragment
# ---------------------------------------------------------------------------


@st.fragment(run_every=timedelta(seconds=_REFRESH_UI))
def _overlay_live_status_fragment() -> None:
    settings = load_settings()
    insts = settings.instances
    if not insts:
        st.warning("No instances in config — cannot pick a rolling preview path.")
        return

    iids = [i.instance_id for i in insts]
    instance_id = st.selectbox("Instance (rolling PNG)", iids, key="pipeline_overlay_instance")
    client = require_redis_connection()
    inst_state = get_instance_state(client, instance_id)
    current_screen = str(inst_state.get("current_screen") or "").strip()

    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    with c1:
        if st.button("Refresh now", use_container_width=True, key="pipeline_overlay_refresh_now"):
            st.session_state["pipeline_force_refresh_nonce"] = force_nonce() + 1
            # Ensure we don't keep stale computed images/results around.
            cache: dict = st.session_state.get(PIPELINE_OVERLAY_CACHE, {})
            if isinstance(cache, dict):
                cache.pop(instance_id, None)
            st.rerun()
    with c2:
        st.caption("Forces overlay re-run even if mtimes didn't change.")

    only_exist = st.checkbox(
        "Only `exist` regions (from `area.json`) ",
        value=False,
        key="pipeline_overlay_exist_only",
        help="When unchecked, every overlay rule row is shown with its region action.",
    )

    cflt1, cflt2 = st.columns([1.4, 1], vertical_alignment="bottom")
    with cflt1:
        name_filter = st.text_input(
            "Filter by zone/rule name",
            value="",
            key="pipeline_overlay_name_filter",
            placeholder="e.g. claim, hand_pointer, isNewPeople…",
        )
    with cflt2:
        only_current_page = st.checkbox(
            "Only current page",
            value=False,
            key="pipeline_overlay_only_current_page",
            help=(
                "When enabled, show only rules whose `node` matches the instance current_screen "
                "(plus global rules without `node`)."
            ),
        )
        st.caption(f"current_screen: `{current_screen or '—'}`")

    data, rebuilt = get_or_build_pipeline_cache(
        instance_id, repo_root=_REPO, area_path=_AREA, analyze_path=_ANALYZE
    )
    if rebuilt:
        # Only show the "loading" UI when we actually rebuilt.
        # Streamlit doesn't support a conditional spinner after-the-fact,
        # so we show a small status line instead.
        st.caption("Updated: rolling PNG + overlay analysis recomputed.")

    if data is None:
        preview_path = rolling_live_preview_path(instance_id)
        if not preview_path.is_file():
            rel = preview_path.relative_to(_REPO)
            st.info(
                f"No rolling file yet: `{rel}` — start the worker or capture from **Instance**."
            )
        else:
            st.warning("Could not decode PNG (corrupt or unreadable).")
        return

    image_bgr: np.ndarray = data["image_bgr"]
    results: dict[str, Any] = data["results"]
    area_doc: dict[str, Any] = data["area_doc"]
    rule_order: list[str] = data["rule_order"]
    rule_search: dict[str, str] = data["rule_search"]
    rule_tap: dict[str, str] = data["rule_tap"]
    rule_node: dict[str, str] = data.get("rule_node", {})

    rows_out: list[dict[str, object]] = []
    visible_logicals: list[str] = []
    q = (name_filter or "").strip().lower()
    for logical in rule_order:
        payload = results.get(logical)
        if not isinstance(payload, dict):
            continue
        # Filter by current page (`node`) when requested.
        node = str(rule_node.get(logical, "") or "").strip()
        if only_current_page and current_screen:
            if node and node != current_screen:
                continue
        region_name = str(payload.get("region") or "").strip()
        area_action = region_area_action(area_doc, region_name)
        if only_exist and area_action != "exist":
            continue
        # Filter by name (rule/region/search/tap) when requested.
        if q:
            hay = " ".join(
                [
                    str(logical),
                    region_name,
                    str(payload.get("search_region") or rule_search.get(logical, "")),
                    str(payload.get("tap_region") or rule_tap.get(logical, "")),
                ]
            ).lower()
            if q not in hay:
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

        # Normalize for Arrow: ensure score/threshold are numeric or None.
        score_f: float | None = None
        if score is not None and str(score).strip() != "":
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                score_f = None

        thr_f: float | None = None
        if thr is not None and str(thr).strip() != "":
            try:
                thr_f = float(thr)
            except (TypeError, ValueError):
                thr_f = None

        rows_out.append(
            {
                "overlay_rule": logical,
                "node": node or "(global)",
                "region": region_name,
                "area_action": area_action if area_action else "(unknown)",
                "search_region": sr_disp,
                "tap_region": tr_disp,
                "status": status,
                # Keep Arrow-friendly types: use None instead of "" so Streamlit doesn't
                # infer a mixed object column and fail conversion.
                "score": None if score_f is None else round(score_f, 4),
                "threshold": thr_f,
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
                vis = annotate_overlay_debug(
                    image_bgr,
                    results,
                    logicals_draw,
                    area_doc,
                    rule_search,
                )
                vis_ui = maybe_downscale_for_ui(vis)
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



# ---------------------------------------------------------------------------
# Page layout (static)
# ---------------------------------------------------------------------------

st.title("Screenshot pipeline")

st.page_link(
    "views/fsm.py",
    label="FSM",
    help="Open the screen transition graph (FSM).",
    width="content",
)

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
    # Read once — reuse for both the parsed table and the raw display in the expander.
    _analyze_raw_text = _ANALYZE.read_text(encoding="utf-8")
    raw = load_analyze_yaml(_ANALYZE)
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
        st.code(_analyze_raw_text, language="yaml")
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
debug_flow_mode_fragment(repo_root=_REPO, area_path=_AREA, analyze_path=_ANALYZE)

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
