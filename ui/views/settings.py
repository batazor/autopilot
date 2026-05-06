"""Global UI settings (ADB paths for screenshots)."""

from __future__ import annotations

import subprocess

import streamlit as st

from capture.adb_screencap import DEFAULT_ADB_BIN, resolve_adb_executable
from ui.settings_state import ensure_ui_settings_session_defaults, get_ui_adb_bin, get_ui_adb_serial

ensure_ui_settings_session_defaults()

st.title("Settings")

st.subheader("ADB")

st.text_input(
    "ADB binary",
    key="wos_settings_adb_bin",
    help="Homebrew (Apple Silicon): /opt/homebrew/bin/adb. Otherwise use `which adb`.",
)
st.text_input(
    "ADB serial (optional)",
    key="wos_settings_adb_serial",
    placeholder="emulator-5554",
    help="If several devices appear in `adb devices`, set the serial from the first column.",
)


def _run_adb_test() -> None:
    adb_bin = get_ui_adb_bin()
    serial = get_ui_adb_serial()

    resolved = resolve_adb_executable(adb_bin)
    if resolved is None:
        st.error(
            f"Binary not found: `{adb_bin}`. "
            "Try `/opt/homebrew/bin/adb` (Homebrew Apple Silicon) "
            "or `~/Library/Android/sdk/platform-tools/adb`."
        )
        return

    st.caption(f"Resolved: `{resolved}`")

    # --- adb devices ---
    try:
        proc = subprocess.run(
            [resolved, "devices", "-l"],
            capture_output=True,
            timeout=8.0,
            check=False,
        )
        output = proc.stdout.decode(errors="replace").strip()
        stderr = proc.stderr.decode(errors="replace").strip()
    except subprocess.TimeoutExpired:
        st.error("**`adb devices`** timed out (8 s). Is the ADB server running?")
        return
    except FileNotFoundError:
        st.error(f"Could not execute `{resolved}` (FileNotFoundError).")
        return

    if stderr:
        st.warning(f"stderr: {stderr}")

    lines = [l for l in output.splitlines() if l.strip()]
    devices = [l for l in lines if l and not l.startswith("List of")]
    if not devices:
        st.warning(
            "No devices found (`adb devices` returned empty list). "
            "Start the emulator or enable USB debugging."
        )
    else:
        st.success(f"`adb devices` — {len(devices)} device(s) connected")
    st.code(output or "(no output)", language="text")

    # --- per-device get-state ---
    if serial:
        try:
            proc2 = subprocess.run(
                [resolved, "-s", serial, "get-state"],
                capture_output=True,
                timeout=5.0,
                check=False,
            )
            state = proc2.stdout.decode(errors="replace").strip()
            err2 = proc2.stderr.decode(errors="replace").strip()
        except subprocess.TimeoutExpired:
            st.error(f"`adb -s {serial} get-state` timed out.")
            return

        if proc2.returncode != 0 or err2:
            st.error(
                f"Serial **`{serial}`** not reachable: "
                f"{err2 or f'exit {proc2.returncode}'}"
            )
        else:
            st.success(f"Serial **`{serial}`** state: `{state}`")


if st.button("Test ADB connection", type="primary"):
    _run_adb_test()

with st.expander("Defaults"):
    st.code(DEFAULT_ADB_BIN, language="text")
