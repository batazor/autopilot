from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import streamlit as st

from actions.tap import BotActions
from config.loader import load_settings
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_to_device_point

from pathlib import Path

from ui.pipeline.data import force_nonce, get_or_build_pipeline_cache
from ui.pipeline.overlay_viz import maybe_downscale_for_ui


def debug_flow_mode_fragment(*, repo_root: Path, area_path: Path, analyze_path: Path) -> None:
    """Interactive debug: step through overlay rules and approve taps."""
    settings = load_settings()
    insts = settings.instances
    if not insts:
        st.warning("No instances in config — cannot debug flow.")
        return

    st.subheader("Debug flow mode")
    st.caption("Step through overlay rules; click only after explicit approval.")

    iids = [i.instance_id for i in insts]
    instance_id = st.selectbox("Instance (debug)", iids, key="pipeline_debug_instance")

    data, _rebuilt = get_or_build_pipeline_cache(
        instance_id, repo_root=repo_root, area_path=area_path, analyze_path=analyze_path
    )
    if data is None:
        st.info("No rolling PNG loaded yet — start the worker or capture a screenshot.")
        return

    results: dict[str, Any] = data["results"]
    area_doc: dict[str, Any] = data["area_doc"]
    rule_order: list[str] = data["rule_order"]
    image_bgr: np.ndarray = data["image_bgr"]

    if not rule_order:
        st.warning("No overlay rules found in `references/analyze.yaml`.")
        return

    idx_key = f"pipeline_debug_rule_idx::{instance_id}"
    idx = int(st.session_state.get(idx_key, 0) or 0)
    idx = max(0, min(len(rule_order) - 1, idx))
    logical = rule_order[idx]
    payload = results.get(logical)

    nav1, nav2, nav3, nav4 = st.columns(4, gap="small")
    with nav1:
        if st.button("⟵ Prev", use_container_width=True, disabled=(idx <= 0)):
            st.session_state[idx_key] = max(0, idx - 1)
            st.rerun()
    with nav2:
        if st.button("Next ⟶", use_container_width=True, disabled=(idx >= len(rule_order) - 1)):
            st.session_state[idx_key] = min(len(rule_order) - 1, idx + 1)
            st.rerun()
    with nav3:
        if st.button("Skip to next Found", use_container_width=True):
            j = idx + 1
            while j < len(rule_order):
                p = results.get(rule_order[j])
                if isinstance(p, dict) and p.get("matched"):
                    break
                j += 1
            st.session_state[idx_key] = min(len(rule_order) - 1, j)
            st.rerun()
    with nav4:
        if st.button("Reset", use_container_width=True):
            st.session_state[idx_key] = 0
            st.rerun()

    st.markdown(f"**Step:** `{idx+1}/{len(rule_order)}` · **Rule:** `{logical}`")
    if not isinstance(payload, dict):
        st.warning("No payload for this rule (maybe YAML parse mismatch).")
        return

    matched = bool(payload.get("matched"))
    region = str(payload.get("region") or "").strip()
    action = str(payload.get("action") or "").strip()
    enqueue_tap = bool(payload.get("enqueue_tap", True))
    score = payload.get("score")
    thr = payload.get("threshold")
    reason = str(payload.get("reason") or "")
    st.write(
        {
            "matched": matched,
            "region": region,
            "action": action,
            "enqueue_tap": enqueue_tap,
            "score": score,
            "threshold": thr,
            "reason": reason,
        }
    )

    with st.expander("Current frame (debug)", expanded=False):
        vis_ui = maybe_downscale_for_ui(image_bgr)
        st.image(cv2.cvtColor(vis_ui, cv2.COLOR_BGR2RGB), width="stretch")

    click_col, info_col = st.columns([1, 3], vertical_alignment="center")
    with click_col:
        st.caption("Approve click (**always manual**).")

        # Pick tap target mode.
        has_payload_pt = payload.get("tap_x_pct") is not None and payload.get("tap_y_pct") is not None
        default_mode = "payload" if has_payload_pt else "manual"
        mode = st.selectbox(
            "Tap target",
            options=["payload", "region", "manual"],
            index=["payload", "region", "manual"].index(default_mode),
            key=f"dbg_tap_mode::{instance_id}",
            help=(
                "`payload`: uses tap_x_pct/tap_y_pct from analysis. "
                "`region`: uses bbox center from area.json. "
                "`manual`: you enter x/y yourself."
            ),
        )

        actions = BotActions()
        dev_w, dev_h = actions.screen_resolution(instance_id)

        # Defaults for manual mode (prefer payload coords, else screen center).
        tx0 = payload.get("tap_x_pct")
        ty0 = payload.get("tap_y_pct")
        default_x_pct = float(tx0) if tx0 is not None else 50.0
        default_y_pct = float(ty0) if ty0 is not None else 50.0

        x_pct = default_x_pct
        y_pct = default_y_pct
        if mode == "manual":
            x_pct = float(
                st.number_input(
                    "X (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_x_pct),
                    step=0.1,
                    key=f"dbg_manual_x::{instance_id}",
                )
            )
            y_pct = float(
                st.number_input(
                    "Y (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_y_pct),
                    step=0.1,
                    key=f"dbg_manual_y::{instance_id}",
                )
            )

        confirm = st.checkbox(
            "I confirm this click",
            value=False,
            key=f"dbg_click_confirm::{instance_id}",
        )

        can_click = confirm
        if st.button(
            "Approve click",
            type="primary",
            use_container_width=True,
            disabled=not can_click,
            help="Will tap the chosen point on the device.",
        ):
            from layout.types import Point

            if mode == "payload":
                tx = payload.get("tap_x_pct")
                ty = payload.get("tap_y_pct")
                if tx is None or ty is None:
                    st.error("No `tap_x_pct/tap_y_pct` in payload; use manual or region.")
                    st.stop()
                x = int(round(float(tx) / 100.0 * dev_w))
                y = int(round(float(ty) / 100.0 * dev_h))
            elif mode == "region":
                pair = screen_region_by_name(area_doc, region)
                if pair is None or not isinstance(pair[1].get("bbox"), dict):
                    st.error("Region bbox not found in `area.json`; use manual.")
                    st.stop()
                pt = bbox_percent_center_to_device_point(pair[1]["bbox"], dev_w, dev_h)
                x, y = int(pt.x), int(pt.y)
            else:
                x = int(round(x_pct / 100.0 * dev_w))
                y = int(round(y_pct / 100.0 * dev_h))

            pt2 = Point(x, y)
            st.success(f"Tapping `{logical}` ({mode}) at ({pt2.x}, {pt2.y}).")
            actions.tap(instance_id, pt2)
            st.session_state["pipeline_force_refresh_nonce"] = force_nonce() + 1
            st.rerun()
    with info_col:
        st.caption(
            "This section is intentionally manual: it can click even when a rule is not matched "
            "or `enqueue_tap: false`. Use it for testing/labeling."
        )

