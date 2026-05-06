"""Global UI settings (ADB paths for screenshots)."""

from __future__ import annotations

import streamlit as st

from capture.adb_screencap import DEFAULT_ADB_BIN
from ui.settings_state import ensure_ui_settings_session_defaults

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

with st.expander("Defaults"):
    st.code(DEFAULT_ADB_BIN, language="text")
