"""Shared Streamlit session keys for UI settings (ADB paths used across Labeling, Instance, annotator)."""

from __future__ import annotations

import streamlit as st

from capture.adb_screencap import DEFAULT_ADB_BIN

_ADB_BIN_KEY = "wos_settings_adb_bin"
_ADB_SERIAL_KEY = "wos_settings_adb_serial"


def ensure_ui_settings_session_defaults() -> None:
    """Initialize or migrate legacy session keys once per browser session."""
    if _ADB_BIN_KEY not in st.session_state:
        migrated: str | None = None
        for legacy in ("area_standalone_adb_bin", "labeling_adb", "detail_adb_bin"):
            v = st.session_state.get(legacy)
            if isinstance(v, str) and v.strip():
                migrated = v.strip()
                break
        st.session_state[_ADB_BIN_KEY] = migrated or DEFAULT_ADB_BIN
    if _ADB_SERIAL_KEY not in st.session_state:
        legacy_s = st.session_state.get("area_standalone_adb_serial")
        st.session_state[_ADB_SERIAL_KEY] = (
            str(legacy_s).strip() if isinstance(legacy_s, str) else ""
        )


def get_ui_adb_bin() -> str:
    ensure_ui_settings_session_defaults()
    v = str(st.session_state.get(_ADB_BIN_KEY, DEFAULT_ADB_BIN)).strip()
    return v or DEFAULT_ADB_BIN


def get_ui_adb_serial() -> str | None:
    ensure_ui_settings_session_defaults()
    s = str(st.session_state.get(_ADB_SERIAL_KEY, "")).strip()
    return s or None
