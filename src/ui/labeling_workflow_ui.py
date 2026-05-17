"""Streamlit widgets for Labeling workflow status."""
from __future__ import annotations

import streamlit as st

from ui.labeling_helpers import LabelingWorkflowStep


def render_labeling_workflow_strip(steps: list[LabelingWorkflowStep]) -> None:
    """Horizontal step indicator (capture → publish → screen → regions → save)."""

    if not steps:
        return
    cols = st.columns(len(steps), gap="small")
    for col, step in zip(cols, steps, strict=True):
        icon = "✓" if step.done else "○"
        tone = "done" if step.done else "pending"
        detail = f" — {step.detail}" if step.detail else ""
        col.markdown(
            f'<div class="labeling-step labeling-step-{tone}">'
            f'<span class="labeling-step-icon">{icon}</span> '
            f"<strong>{step.label}</strong>"
            f'<span class="labeling-step-detail">{detail}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        """
        <style>
        .labeling-step {
            font-size: 0.82rem;
            line-height: 1.35;
            padding: 0.35rem 0.5rem;
            border-radius: 0.5rem;
            border: 1px solid rgba(148, 163, 184, 0.35);
            background: rgba(248, 250, 252, 0.6);
        }
        .labeling-step-done {
            border-color: rgba(16, 185, 129, 0.45);
            background: rgba(16, 185, 129, 0.08);
        }
        .labeling-step-pending .labeling-step-icon { opacity: 0.55; }
        .labeling-step-detail {
            display: block;
            font-size: 0.72rem;
            color: #64748b;
            margin-top: 0.15rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
