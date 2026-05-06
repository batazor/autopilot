"""Documentation: what the worker runs on each rolling ADB capture."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import cv2
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
    for logical in rule_order:
        payload = results.get(logical)
        if not isinstance(payload, dict):
            continue
        region_name = str(payload.get("region") or "").strip()
        area_action = _region_area_action(area_doc, region_name)
        if only_exist and area_action != "exist":
            continue

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

    if not rows_out:
        st.info("No overlay rows to show (check YAML or turn off the `exist` filter).")
        return

    st.dataframe(rows_out, hide_index=True, use_container_width=True)


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
   Tap: **`tap_region`** if set; else match centre (**`search_region`**); else template bbox centre.
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
        st.dataframe(rows_static, hide_index=True, use_container_width=True)
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
For **`exist`**, **Found** / **Not found** = template score vs overlay threshold.
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
