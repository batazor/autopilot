from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from config.paths import repo_root as default_repo_root
from ui.ia_overlay_executor import ensure_ia_overlay_analyzer
from ui.ia_preview_service import ensure_ia_preview_refresher
from ui.ia_queue_executor import ensure_ia_queue_executor

logging.getLogger(
    "streamlit.runtime.scriptrunner_utils.script_run_context"
).setLevel(logging.ERROR)

_ui_dir = Path(__file__).resolve().parent
_repo_root = default_repo_root()
_logo_path = _repo_root / "docs" / "logo.png"
_logo_icon_path = _repo_root / "docs" / "logo_icon.png"

st.set_page_config(
    page_title="WOS IA Editor",
    layout="wide",
    page_icon=str(_logo_icon_path if _logo_icon_path.exists() else _logo_path),
)

if _logo_path.exists():
    st.logo(
        str(_logo_path),
        size="large",
        icon_image=str(_logo_icon_path) if _logo_icon_path.exists() else None,
        link="https://github.com/batazor/whiteout-survival-autopilot",
    )

st.caption(
    "IA editor mode: embedded bot workers, scheduler, overlay loop, and health watchdog "
    "are disabled. A lightweight preview thread refreshes screenshots and current node; "
    "manual queue scenarios run through an isolated IA executor plus Click approvals."
)
ensure_ia_preview_refresher()
ensure_ia_queue_executor()
ensure_ia_overlay_analyzer()

click_approvals_page = st.Page(
    str(_ui_dir / "views" / "click_approvals.py"),
    title="Click approvals",
    default=True,
)
labeling_page = st.Page(
    str(_ui_dir / "views" / "labeling.py"),
    title="Labeling",
    url_path="labeling",
)
routes_page = st.Page(
    str(_ui_dir / "views" / "routes.py"),
    title="Routes",
    url_path="routes",
)
queue_page = st.Page(
    str(_ui_dir / "views" / "queue.py"),
    title="Queue",
    url_path="queue",
)
adb_page = st.Page(str(_ui_dir / "views" / "adb_devices.py"), title="ADB", url_path="adb")

st.navigation(
    {
        "Rehearsal": [click_approvals_page, queue_page],
        "Authoring": [labeling_page],
        "Debug": [routes_page, adb_page],
    }
).run()
