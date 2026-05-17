from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from config.paths import repo_root as default_repo_root
from ui.bot_services import ensure_embedded_bot
from ui.module_pages import extend_nav_pages, module_streamlit_pages_by_nav

# ``@st.fragment(run_every=…)`` ticks run on Streamlit's thread pool; without a
# ScriptRunContext they emit harmless "bare mode" warnings that drown bot logs.
logging.getLogger(
    "streamlit.runtime.scriptrunner_utils.script_run_context"
).setLevel(logging.ERROR)

_ui_dir = Path(__file__).resolve().parent
_repo_root = default_repo_root()
_logo_path = _repo_root / "docs" / "logo.png"
_logo_icon_path = _repo_root / "docs" / "logo_icon.png"

st.set_page_config(
    page_title="WOS Bot",
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

ensure_embedded_bot()

overview = st.Page(str(_ui_dir / "views" / "overview.py"), title="Overview", default=True)
instance_page = st.Page(str(_ui_dir / "views" / "instance.py"), title="Instance")
player_state_page = st.Page(
    str(_ui_dir / "views" / "player_state.py"),
    title="Player state",
    url_path="player_state",
)
gallery_page = st.Page(str(_ui_dir / "views" / "gallery.py"), title="Gallery")
click_approvals_page = st.Page(
    str(_ui_dir / "views" / "click_approvals.py"),
    title="Click approvals",
)
queue_page = st.Page(str(_ui_dir / "views" / "queue.py"), title="Queue")
debug_scenarios_page = st.Page(
    str(_ui_dir / "views" / "debug_scenarios.py"),
    title="Scenario runner",
    url_path="debug_scenarios",
)
scenarios_page = st.Page(
    str(_ui_dir / "views" / "scenarios.py"),
    title="Scenarios",
    url_path="scenarios",
)
edit_scenarios_page = st.Page(
    str(_ui_dir / "views" / "edit_scenarios.py"),
    title="Scenarios editor",
    url_path="edit_scenarios",
)
wiki_analyze_page = st.Page(str(_ui_dir / "views" / "wiki_analyze.py"), title="Analyze")
labeling_page = st.Page(str(_ui_dir / "views" / "labeling.py"), title="Labeling")
wiki_db_page = st.Page(str(_ui_dir / "views" / "wiki_db.py"), title="Wiki reference")
routes_page = st.Page(
    str(_ui_dir / "views" / "routes.py"),
    title="Routes",
    url_path="routes",
)
adb_page = st.Page(str(_ui_dir / "views" / "adb_devices.py"), title="ADB", url_path="adb")
balance_page = st.Page(
    str(_ui_dir / "views" / "balance.py"), title="Balance", url_path="balance"
)
optimizer_debug_page = st.Page(
    str(_ui_dir / "views" / "optimizer_debug.py"),
    title="Optimizer",
    url_path="optimizer",
)
_nav = extend_nav_pages(
    {
        "Operate": [overview, instance_page, player_state_page],
        "DB": [wiki_db_page],
        "Wiki": [
            gallery_page,
            labeling_page,
            edit_scenarios_page,
            wiki_analyze_page,
        ],
        "Debug": [
            click_approvals_page,
            queue_page,
            debug_scenarios_page,
            routes_page,
            optimizer_debug_page,
        ],
        "Config": [scenarios_page, adb_page, balance_page],
    },
    module_streamlit_pages_by_nav(_repo_root),
)

st.navigation(_nav).run()
