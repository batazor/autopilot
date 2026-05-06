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
screenshot_pipeline_page = st.Page(
    str(_ui_dir / "views" / "screenshot_pipeline.py"),
    title="Screenshot pipeline",
)
queue_page = st.Page(str(_ui_dir / "views" / "queue.py"), title="Queue")
scenarios_page = st.Page(str(_ui_dir / "views" / "scenarios.py"), title="Scenarios")
labeling_page = st.Page(str(_ui_dir / "views" / "labeling.py"), title="Labeling")
fsm_page = st.Page(str(_ui_dir / "views" / "fsm.py"), title="FSM")
settings_page = st.Page(str(_ui_dir / "views" / "settings.py"), title="Settings")

st.navigation(
    [
        overview,
        instance_page,
        screenshot_pipeline_page,
        labeling_page,
        fsm_page,
        queue_page,
        scenarios_page,
        settings_page,
    ],
).run()
