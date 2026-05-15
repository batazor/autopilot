"""Optimizer — production (live state) and playground (synthetic what-if) in one page."""

from __future__ import annotations

import streamlit as st

from optimizer.context import invalidate_balance_context
from ui.views.optimizer_playground_panel import render_playground_panel
from ui.views.optimizer_production_panel import render_production_panel
from ui.views.optimizer_ui import render_optimizer_nav

st.title("Optimizer")
render_optimizer_nav()

with st.sidebar:
    st.subheader("Balance configs")
    if st.button("Reload balance configs", width="stretch", key="optimizer_reload_balance"):
        invalidate_balance_context()
        st.success("Balance cache cleared.")
        st.rerun()

tab_prod, tab_play = st.tabs(["Production", "Playground"])

with tab_prod:
    render_production_panel()

with tab_play:
    render_playground_panel()
