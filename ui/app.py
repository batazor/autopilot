"""Single app: Streamlit UI + bot (workers and scheduler) in one process."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="WOS Bot", layout="wide")


@st.cache_resource
def _start_bot_services() -> bool:
    """One background thread running asyncio: all InstanceWorkers and SchedulerRunner."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    import asyncio

    from worker.async_supervisor import run_forever_async

    def _run_loop() -> None:
        asyncio.run(run_forever_async())

    threading.Thread(target=_run_loop, daemon=True, name="wos-async-services").start()
    return True


_start_bot_services()

_ui_dir = Path(__file__).parent

overview = st.Page(str(_ui_dir / "views" / "overview.py"), title="Overview", default=True)
instance_page = st.Page(str(_ui_dir / "views" / "instance.py"), title="Instance")
queue_page = st.Page(str(_ui_dir / "views" / "queue.py"), title="Queue")
logs_page = st.Page(str(_ui_dir / "views" / "logs.py"), title="Logs")
scenarios_page = st.Page(str(_ui_dir / "views" / "scenarios.py"), title="Scenarios")
labeling_page = st.Page(str(_ui_dir / "views" / "labeling.py"), title="Labeling")
fsm_page = st.Page(str(_ui_dir / "views" / "fsm.py"), title="FSM")
settings_page = st.Page(str(_ui_dir / "views" / "settings.py"), title="Settings")

st.navigation(
    [
        overview,
        instance_page,
        labeling_page,
        fsm_page,
        queue_page,
        logs_page,
        scenarios_page,
        settings_page,
    ],
).run()
