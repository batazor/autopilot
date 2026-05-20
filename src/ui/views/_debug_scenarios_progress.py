"""Always-on step progress fragment for the Debug Scenarios page.

Rendered both in the normal (idle / running) view and during a pending
click-approval card so the operator never loses the step counter when the
right column flips from "Run status" to the approval prompt. Lives in its
own module so the main page file stays focused on layout and routing.

Not registered as a Streamlit page (``ui/app.py`` lists pages explicitly
via ``st.Page``), so the leading underscore is purely a "don't import this
as a script" hint to readers.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import streamlit as st

from dsl import template_resolver as _tmpl
from tasks.dsl_scenario_helpers import _dsl_step_summary
from ui.redis_client import (
    fetch_running_queue_row,
    get_instance_state,
)

if TYPE_CHECKING:
    from pathlib import Path

    import redis


@st.fragment(run_every=timedelta(seconds=1))
def render_step_progress(
    *,
    client: redis.Redis,
    instance_id: str,
    scenario_key: str,
    scenario_total_steps: int,
) -> None:
    """Live ``Step X/N`` bar; shows ``0/N · idle`` when no matching task runs.

    The bar always renders even when the selected scenario isn't the one
    currently executing — keeps the section anchored so the layout doesn't
    jump when a debug run finishes or an approval card pops up.
    """
    state = get_instance_state(client, instance_id)
    running = fetch_running_queue_row(client, instance_id=instance_id)
    active = str(state.get("current_scenario") or "").strip() == scenario_key
    busy = str(state.get("state") or "").strip().lower() == "busy"
    task_type = str(state.get("current_task_type") or "").strip()
    has_task = bool(str(state.get("current_task_id") or "").strip())
    is_running = bool(
        active
        and (
            (
                running is not None
                and running.task_id
                and running.task_type == scenario_key
            )
            or (busy and has_task and (task_type == scenario_key or not task_type))
        )
    )
    step_display = 0
    step_iter = 0
    if scenario_total_steps > 0:
        try:
            step_now = int(state.get("last_active_scenario_step") or 0)
        except (TypeError, ValueError):
            step_now = 0
        cap = (scenario_total_steps - 1) if is_running else scenario_total_steps
        step_display = max(0, min(step_now, cap))
        try:
            step_iter = int(state.get("last_active_scenario_iter") or 0)
        except (TypeError, ValueError):
            step_iter = 0
    completed = (step_display + 1) if is_running else step_display
    ratio = completed / max(1, scenario_total_steps)
    if scenario_total_steps > 0:
        text = f"Step {step_display + 1}/{scenario_total_steps}"
        if is_running and step_iter > 0:
            text += f" · iter {step_iter}"
        if not is_running:
            text += " · idle"
    else:
        text = "no steps"
    nav_target = str(state.get("nav_target") or "").strip() if is_running else ""
    if nav_target:
        text += f" · navigating → {nav_target}"
    st.progress(min(1.0, max(0.0, ratio)), text=text)


def _load_scenario_step_summaries(repo_root: Path, key: str) -> tuple[str, ...]:
    """One short summary per top-level step of the scenario for ``key``.

    Goes through ``template_resolver.load_doc`` so template-driven keys like
    ``level_up_ahmose`` are rendered (with ``${hero_name}`` substituted)
    before steps are extracted — otherwise the function would not find
    ``level_up_ahmose.yaml`` on disk and returns ``()``.

    Returns ``()`` when the scenario is missing, malformed, or has no
    ``steps:`` list. Underlying ``load_doc`` already caches by ``mtime``.
    """
    if not key:
        return ()
    loaded = _tmpl.load_doc(repo_root, key)
    if loaded is None:
        return ()
    _path, raw = loaded
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return ()
    return tuple(_dsl_step_summary(s) for s in steps)


def _render_step_list_caption(
    summaries: tuple[str, ...], current_step: int, is_running: bool
) -> None:
    """One-line, light-grey, small-font caption with all top-level step
    summaries joined by ``·``. The currently-running step is bolded so it
    stands out within the grey strip. ``current_step`` is 0-based;
    ``is_running`` controls whether the bold marker is applied.
    """
    if not summaries:
        return
    parts: list[str] = []
    for i, summary in enumerate(summaries):
        if is_running and i == current_step:
            parts.append(f"**{summary}**")
        else:
            parts.append(summary)
    st.caption(" · ".join(parts))


@st.fragment(run_every=timedelta(seconds=1))
def render_active_scenario_progress(
    *,
    client: redis.Redis,
    instance_id: str,
    repo_root: Path,
) -> None:
    """Self-contained progress bar: discovers the currently-running scenario
    from Redis state and renders its ``Step X/N`` plus a light-grey list of
    top-level step summaries with the active step highlighted.

    Used on pages that don't have a user-selected scenario (e.g. the global
    Click Approvals page), so the operator still sees where the worker is in
    the active scenario while approving a pending click. Renders an idle bar
    when nothing is running so the layout doesn't jump.
    """
    state = get_instance_state(client, instance_id)
    running = fetch_running_queue_row(client, instance_id=instance_id)
    active_scenario = str(state.get("current_scenario") or "").strip()
    summaries = (
        _load_scenario_step_summaries(repo_root, active_scenario)
        if active_scenario
        else ()
    )
    total = len(summaries)
    busy = str(state.get("state") or "").strip().lower() == "busy"
    task_type = str(state.get("current_task_type") or "").strip()
    has_task = bool(str(state.get("current_task_id") or "").strip())
    is_running = bool(
        active_scenario
        and (
            (
                running is not None
                and running.task_id
                and running.task_type == active_scenario
            )
            or (busy and has_task and (task_type == active_scenario or not task_type))
        )
    )
    step_display = 0
    step_iter = 0
    if total > 0:
        try:
            step_now = int(state.get("last_active_scenario_step") or 0)
        except (TypeError, ValueError):
            step_now = 0
        cap = (total - 1) if is_running else total
        step_display = max(0, min(step_now, cap))
        try:
            step_iter = int(state.get("last_active_scenario_iter") or 0)
        except (TypeError, ValueError):
            step_iter = 0
    completed = (step_display + 1) if is_running else step_display
    ratio = completed / max(1, total)
    if active_scenario and total > 0:
        text = f"{active_scenario} · Step {step_display + 1}/{total}"
        if is_running and step_iter > 0:
            text += f" · iter {step_iter}"
        if not is_running:
            text += " · idle"
    elif active_scenario:
        text = f"{active_scenario} · running"
    else:
        text = "no active scenario"
    nav_target = str(state.get("nav_target") or "").strip() if is_running else ""
    if nav_target:
        text += f" · navigating → {nav_target}"
    st.progress(min(1.0, max(0.0, ratio)), text=text)
