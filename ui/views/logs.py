"""Per-instance log tail from Redis."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config.loader import load_settings
from ui.redis_client import clear_logs, fetch_logs, require_redis_connection

st_autorefresh(interval=2000, key="logs_refresh")

st.title("Logs")

settings = load_settings()
client = require_redis_connection()

ids = [i.instance_id for i in settings.instances]
if not ids:
    st.warning("No instances configured.")
    st.stop()

instance_id = st.selectbox("Instance", ids)
filt = st.text_input("Filter (substring)", "")

lines = fetch_logs(client, instance_id)
if filt:
    lines = [ln for ln in lines if filt.lower() in ln.lower()]

if st.button("Clear logs"):
    clear_logs(client, instance_id)
    st.rerun()

if not lines:
    st.code("(empty)")
else:
    st.code("\n".join(reversed(lines)))
