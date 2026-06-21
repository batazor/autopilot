"""Shared scenario step progress math for API + Streamlit + queue UI."""
from __future__ import annotations


def compute_scenario_progress_metrics(
    *,
    step_current: int,
    step_total: int,
    is_running: bool,
    nav_target: str,
) -> dict[str, int | float | bool]:
    """Map Redis step index + nav phase to bar fill and highlight index.

    While ``nav_target`` is set the runner has not started (or resumed) the
    current DSL step — do not treat ``step_current + 1`` as completed work.
    """
    navigating = bool(is_running and str(nav_target or "").strip())
    total = max(0, int(step_total))
    cur = max(0, int(step_current))
    if total <= 0:
        return {
            "is_navigating": navigating,
            "completed_steps": 0,
            "progress_ratio": 0.0,
            "highlight_step_index": -1,
        }
    if navigating:
        completed = min(cur, total)
        highlight = -1
    elif is_running:
        completed = min(cur + 1, total)
        highlight = min(cur, total - 1)
    else:
        completed = min(cur, total)
        highlight = -1
    return {
        "is_navigating": navigating,
        "completed_steps": completed,
        "progress_ratio": completed / total,
        "highlight_step_index": highlight,
    }


def format_scenario_progress_label(
    *,
    scenario_label: str,
    scenario_key: str,
    step_current: int,
    step_total: int,
    step_iter: int,
    is_running: bool,
    is_navigating: bool,
    nav_target: str,
) -> str:
    """Human-readable progress line (Streamlit ``st.progress`` text parity)."""
    name = (scenario_label or scenario_key or "").strip()
    if not name:
        return "no active scenario"
    nav = str(nav_target or "").strip()
    total = max(0, int(step_total))
    cur = max(0, int(step_current))
    if is_navigating and nav:
        text = f"{name} · Navigating → {nav}"
        if total > 0 and cur > 0:
            text = f"{name} · Step {cur}/{total} · Navigating → {nav}"
        return text
    if total > 0:
        text = f"{name} · Step {cur + 1}/{total}"
        if is_running and step_iter > 0:
            text += f" · iter {step_iter}"
        if not is_running:
            text += " · idle"
        return text
    return f"{name} · running"
