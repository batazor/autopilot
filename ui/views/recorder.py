"""Scenario recorder: tap/swipe on a live screenshot, mirror to device via ADB,
emit a DSL YAML scenario with raw-coord ``tap:`` / ``swipe:`` / ``wait:`` steps.

Live mode + percent-of-screen coords + bypass click_approval (operator-driven
input, prompts would double-confirm every gesture).
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import streamlit as st
import yaml
from PIL import Image

from ui.streamlit_canvas_compat import ensure_drawable_canvas_compat

ensure_drawable_canvas_compat()

from streamlit_drawable_canvas import st_canvas

from capture.adb_screencap import DEFAULT_ADB_BIN, adb_screencap_bgr, resolve_adb_executable
from config.loader import get_settings

st.title("🎬 Scenario recorder")
st.caption(
    "Tap / drag on the live screenshot. Each gesture is executed on the device "
    "immediately and appended as a step. Export to `scenarios/drafts/<name>.yaml` "
    "when done."
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_settings = get_settings()
_instances = [inst.instance_id for inst in _settings.instances]
if not _instances:
    st.error("No instances configured. Add one in `config/settings.yaml`.")
    st.stop()


def _serial_for(instance_id: str) -> str:
    for inst in _settings.instances:
        if inst.instance_id == instance_id:
            return inst.bluestacks_window_title
    raise ValueError(f"Unknown instance_id: {instance_id!r}")


def _adb_bin() -> str:
    pref = (_settings.worker.adb_executable or "").strip()
    resolved = resolve_adb_executable(pref or "adb")
    return resolved or DEFAULT_ADB_BIN


def _run_adb(argv: list[str]) -> tuple[bool, str]:
    """Returns (success, error_message). ``error_message`` empty on success."""
    try:
        res = subprocess.run(
            argv, capture_output=True, text=True, timeout=5.0,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip() or f"exit {res.returncode}"
        return False, err
    return True, ""


def _adb_tap(serial: str, x: int, y: int) -> tuple[bool, str]:
    return _run_adb(
        [_adb_bin(), "-s", serial, "shell", "input", "tap", str(x), str(y)]
    )


def _adb_swipe(
    serial: str, x1: int, y1: int, x2: int, y2: int, ms: int
) -> tuple[bool, str]:
    return _run_adb(
        [
            _adb_bin(), "-s", serial, "shell",
            "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(int(ms)),
        ]
    )


def _capture(instance_id: str) -> tuple[Image.Image, int, int] | None:
    """Live framebuffer → (PIL.Image RGB, dev_w, dev_h). ``None`` if ADB is down."""
    img_bgr, err = adb_screencap_bgr(_adb_bin(), _serial_for(instance_id))
    if img_bgr is None:
        st.error(f"ADB screencap failed: {err}")
        return None
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb), int(img_bgr.shape[1]), int(img_bgr.shape[0])


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
_DEFAULT_NAME = "recorded_scenario"

if "recorder_steps" not in st.session_state:
    st.session_state.recorder_steps = []  # type: list[dict[str, Any]]
if "recorder_canvas_rev" not in st.session_state:
    # Bumped after each processed gesture so the next st_canvas gets a fresh
    # key and the just-drawn shape doesn't persist visually (and doesn't get
    # re-processed on the next rerun).
    st.session_state.recorder_canvas_rev = 0
if "recorder_last_action_ts" not in st.session_state:
    st.session_state.recorder_last_action_ts = None
if "recorder_inst" not in st.session_state:
    st.session_state.recorder_inst = _instances[0]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Recorder config")
    instance_id = st.selectbox(
        "Instance",
        _instances,
        key="recorder_inst",
    )
    scenario_filename = st.text_input(
        "Scenario filename (without `.yaml`)",
        value=_DEFAULT_NAME,
        help="Saved to `scenarios/drafts/<name>.yaml` on Export.",
    )
    scenario_display_name = st.text_input(
        "Scenario display name",
        value="🎬 Recorded scenario",
    )
    auto_wait = st.checkbox(
        "Auto-insert wait between actions",
        value=True,
        help="Inserts a `wait:` step measuring real time elapsed between gestures.",
    )
    tool = st.radio(
        "Tool",
        ["Tap (point)", "Swipe (line)"],
        horizontal=False,
        help="Tap draws a single point. Swipe drags a line — start → end.",
    )


# ---------------------------------------------------------------------------
# Capture + canvas
# ---------------------------------------------------------------------------
col_canvas, col_steps = st.columns([3, 2])

with col_canvas:
    refresh_clicked = st.button("📸 Refresh screenshot", width="stretch")
    captured = _capture(instance_id)
    if captured is None:
        st.stop()
    bg, dev_w, dev_h = captured

    # Render canvas at a comfortable on-screen size while preserving aspect.
    canvas_w = 360
    canvas_h = max(1, int(round(canvas_w * dev_h / max(1, dev_w))))

    drawing_mode = "point" if tool.startswith("Tap") else "line"
    canvas_key = (
        f"recorder_canvas_{instance_id}_{drawing_mode}_"
        f"{st.session_state.recorder_canvas_rev}"
    )
    canvas_result = st_canvas(
        fill_color="rgba(255, 165, 0, 0.3)",
        stroke_width=4,
        stroke_color="#fa0",
        background_image=bg,
        update_streamlit=True,
        height=canvas_h,
        width=canvas_w,
        drawing_mode=drawing_mode,
        key=canvas_key,
    )

    # ----- Gesture extraction -----
    new_step: dict[str, Any] | None = None
    last_action_ts = st.session_state.recorder_last_action_ts
    now = time.monotonic()

    if canvas_result and canvas_result.json_data:
        objs = canvas_result.json_data.get("objects") or []
        if objs:
            obj = objs[-1]
            kind = obj.get("type")
            if drawing_mode == "point" and kind in {"circle", "point"}:
                # Centre of the small circle marker.
                cx = float(obj.get("left", 0.0)) + float(obj.get("radius", 0.0))
                cy = float(obj.get("top", 0.0)) + float(obj.get("radius", 0.0))
                x_pct = max(0.0, min(100.0, cx / canvas_w * 100.0))
                y_pct = max(0.0, min(100.0, cy / canvas_h * 100.0))
                px = int(round(x_pct / 100.0 * dev_w))
                py = int(round(y_pct / 100.0 * dev_h))
                ok, err = _adb_tap(_serial_for(instance_id), px, py)
                if not ok:
                    st.error(f"ADB tap failed — step not recorded: {err}")
                    st.session_state.recorder_canvas_rev += 1
                else:
                    new_step = {
                        "tap": {
                            "x_pct": round(x_pct, 2),
                            "y_pct": round(y_pct, 2),
                        }
                    }
            elif drawing_mode == "line" and kind == "line":
                # Fabric.js stores ``line`` as (left, top) + (x1, y1, x2, y2)
                # where (x1, y1) and (x2, y2) are relative to the line's
                # bounding-box origin. Absolute endpoints = (left + xN, top + yN).
                left = float(obj.get("left", 0.0))
                top = float(obj.get("top", 0.0))
                x1 = left + float(obj.get("x1", 0.0))
                y1 = top + float(obj.get("y1", 0.0))
                x2 = left + float(obj.get("x2", 0.0))
                y2 = top + float(obj.get("y2", 0.0))
                x1_pct = max(0.0, min(100.0, x1 / canvas_w * 100.0))
                y1_pct = max(0.0, min(100.0, y1 / canvas_h * 100.0))
                x2_pct = max(0.0, min(100.0, x2 / canvas_w * 100.0))
                y2_pct = max(0.0, min(100.0, y2 / canvas_h * 100.0))
                px1 = int(round(x1_pct / 100.0 * dev_w))
                py1 = int(round(y1_pct / 100.0 * dev_h))
                px2 = int(round(x2_pct / 100.0 * dev_w))
                py2 = int(round(y2_pct / 100.0 * dev_h))
                swipe_ms = 400
                ok, err = _adb_swipe(
                    _serial_for(instance_id), px1, py1, px2, py2, swipe_ms
                )
                if not ok:
                    st.error(f"ADB swipe failed — step not recorded: {err}")
                    st.session_state.recorder_canvas_rev += 1
                else:
                    new_step = {
                        "swipe": {
                            "x1_pct": round(x1_pct, 2),
                            "y1_pct": round(y1_pct, 2),
                            "x2_pct": round(x2_pct, 2),
                            "y2_pct": round(y2_pct, 2),
                            "ms": swipe_ms,
                        }
                    }

    if new_step is not None:
        # Auto-wait based on real elapsed time since the last gesture.
        if auto_wait and last_action_ts is not None:
            elapsed = max(0.0, now - last_action_ts)
            if elapsed >= 0.3:
                wait_ms = int(round(elapsed * 1000))
                st.session_state.recorder_steps.append({"wait": f"{wait_ms}ms"})
        st.session_state.recorder_steps.append(new_step)
        st.session_state.recorder_last_action_ts = now
        st.session_state.recorder_canvas_rev += 1
        # Rerun so the canvas remounts with a fresh key + we capture an updated
        # screenshot reflecting the gesture's effect on the game.
        st.rerun()

# ---------------------------------------------------------------------------
# Steps panel
# ---------------------------------------------------------------------------
with col_steps:
    st.subheader(f"Steps ({len(st.session_state.recorder_steps)})")
    steps = st.session_state.recorder_steps

    if not steps:
        st.caption("Draw on the screenshot to record gestures.")
    else:
        for idx, step in enumerate(list(steps)):
            row = st.columns([5, 1, 1, 1])
            with row[0]:
                if "tap" in step:
                    t = step["tap"]
                    st.write(
                        f"**{idx + 1}. tap** · x={t['x_pct']}% y={t['y_pct']}%"
                    )
                elif "swipe" in step:
                    s = step["swipe"]
                    st.write(
                        f"**{idx + 1}. swipe** · "
                        f"({s['x1_pct']}%, {s['y1_pct']}%) → "
                        f"({s['x2_pct']}%, {s['y2_pct']}%) · {s.get('ms', 400)}ms"
                    )
                elif "wait" in step:
                    st.write(f"**{idx + 1}. wait** · {step['wait']}")
                else:
                    st.write(f"**{idx + 1}.** {step!r}")
            with row[1]:
                if st.button("↑", key=f"up_{idx}", disabled=idx == 0):
                    steps[idx - 1], steps[idx] = steps[idx], steps[idx - 1]
                    st.rerun()
            with row[2]:
                if st.button(
                    "↓", key=f"dn_{idx}", disabled=idx == len(steps) - 1
                ):
                    steps[idx + 1], steps[idx] = steps[idx], steps[idx + 1]
                    st.rerun()
            with row[3]:
                if st.button("🗑️", key=f"del_{idx}"):
                    steps.pop(idx)
                    st.rerun()

    st.divider()

    actions_row = st.columns([1, 1])
    with actions_row[0]:
        wait_ms_value = st.number_input(
            "Wait (ms)", min_value=50, max_value=60_000, value=500, step=50,
            label_visibility="collapsed",
        )
        if st.button("➕ Add wait", width="stretch"):
            steps.append({"wait": f"{int(wait_ms_value)}ms"})
            st.rerun()
    with actions_row[1]:
        if st.button("🧹 Clear all", width="stretch"):
            st.session_state.recorder_steps = []
            st.session_state.recorder_last_action_ts = None
            st.session_state.recorder_canvas_rev += 1
            st.rerun()

    st.divider()

    if st.button("💾 Export YAML", type="primary", width="stretch"):
        if not steps:
            st.warning("No steps recorded.")
        else:
            out_dir = (Path(__file__).resolve().parents[2] / "scenarios" / "drafts").resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            raw_name = Path(scenario_filename.strip()).name
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._-") or _DEFAULT_NAME
            out_path = (out_dir / f"{safe_name}.yaml").resolve()
            if not out_path.is_relative_to(out_dir):
                st.error(f"Refusing to write outside `{out_dir}` (got `{out_path}`).")
                st.stop()
            doc = {
                "enabled": False,
                "name": scenario_display_name,
                "device_level": True,
                "steps": list(steps),
            }
            out_path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            shown_path = (
                out_path.relative_to(Path.cwd())
                if out_path.is_relative_to(Path.cwd())
                else out_path
            )
            st.success(f"Saved → {shown_path}")
            with st.expander("Preview YAML", expanded=False):
                st.code(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), language="yaml")
