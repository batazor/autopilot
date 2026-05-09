"""Single app: Streamlit UI + bot (workers and scheduler) in one process."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui.bot_services import ensure_embedded_bot

st.set_page_config(page_title="WOS Bot", layout="wide")

ensure_embedded_bot()

_ui_dir = Path(__file__).parent

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
scenarios_page = st.Page(str(_ui_dir / "views" / "scenarios.py"), title="Scenarios")
wiki_scenarios_page = st.Page(str(_ui_dir / "views" / "wiki_scenarios.py"), title="Scenarios")
wiki_analyze_page = st.Page(str(_ui_dir / "views" / "wiki_analyze.py"), title="Analyze")
labeling_page = st.Page(str(_ui_dir / "views" / "labeling.py"), title="Labeling")
db_gift_codes_page = st.Page(str(_ui_dir / "views" / "db_gift_codes.py"), title="Gift codes")
wiki_db_page = st.Page(str(_ui_dir / "views" / "wiki_db.py"), title="Wiki reference")
fsm_page = st.Page(
    str(_ui_dir / "views" / "fsm.py"),
    title="Routes",
    url_path="routes",
)
settings_page = st.Page(str(_ui_dir / "views" / "settings.py"), title="Settings")

st.navigation(
    {
        "Operate": [overview, instance_page, player_state_page],
        "DB": [db_gift_codes_page, wiki_db_page],
        "Wiki": [
            gallery_page,
            labeling_page,
            wiki_scenarios_page,
            wiki_analyze_page,
        ],
        "Debug": [click_approvals_page, queue_page, fsm_page],
        "Config": [scenarios_page, settings_page],
    }
).run()
