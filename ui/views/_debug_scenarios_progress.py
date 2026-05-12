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
from functools import lru_cache
from pathlib import Path

import redis
import streamlit as st
import yaml

from tasks.dsl_scenario_helpers import _dsl_step_summary
from ui.redis_client import (
    fetch_running_queue_row,
    get_instance_state,
)


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
    is_running = bool(
        running is not None
        and running.task_id
        and running.task_type == scenario_key
        and str(state.get("current_scenario") or "").strip() == scenario_key
    )
    step_display = 0
    if is_running and scenario_total_steps > 0:
        try:
            step_now = int(state.get("last_active_scenario_step") or 0)
        except (TypeError, ValueError):
            step_now = 0
        step_display = max(0, min(step_now, scenario_total_steps))
    ratio = step_display / max(1, scenario_total_steps)
    if scenario_total_steps > 0:
        text = f"Step {step_display}/{scenario_total_steps}"
        if not is_running:
            text += " · idle"
    else:
        text = "no steps"
    st.progress(min(1.0, max(0.0, ratio)), text=text)


@lru_cache(maxsize=256)
def _load_scenario_step_summaries_cached(
    repo_str: str, key: str, mtime_ns: int
) -> tuple[str, ...]:
    """Disk-backed top-level step summaries for a scenario YAML. Cache key
    includes ``mtime_ns`` so edits invalidate transparently — same trick as
    :func:`tasks.dsl_scenario_helpers._load_yaml_cached`. Returns an empty
    tuple when the file is missing or malformed.
    """
    repo = Path(repo_str)
    scenarios_root = repo / "scenarios"
    if not scenarios_root.is_dir():
        return ()
    hits = [
        p for p in scenarios_root.rglob(f"{key}.yaml")
        if not p.relative_to(scenarios_root).as_posix().startswith("drafts/")
    ]
    if not hits:
        return ()
    hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
    try:
        raw = yaml.safe_load(hits[0].read_text(encoding="utf-8")) or {}
    except Exception:
        return ()
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return ()
    return tuple(_dsl_step_summary(s) for s in steps)


def _load_scenario_step_summaries(repo_root: Path, key: str) -> tuple[str, ...]:
    """Stat a scenario by ``key`` and return one short summary per top-level
    step. Returns ``()`` when the file is missing, malformed, or has no
    ``steps:`` list.
    """
    if not key:
        return ()
    scenarios_root = repo_root / "scenarios"
    if not scenarios_root.is_dir():
        return ()
    hits = [
        p for p in scenarios_root.rglob(f"{key}.yaml")
        if not p.relative_to(scenarios_root).as_posix().startswith("drafts/")
    ]
    if not hits:
        return ()
    hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
    try:
        st_ns = hits[0].stat().st_mtime_ns
    except OSError:
        return ()
    return _load_scenario_step_summaries_cached(str(repo_root), key, st_ns)


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
    is_running = bool(
        running is not None
        and running.task_id
        and active_scenario
        and running.task_type == active_scenario
    )
    step_display = 0
    if is_running and total > 0:
        try:
            step_now = int(state.get("last_active_scenario_step") or 0)
        except (TypeError, ValueError):
            step_now = 0
        step_display = max(0, min(step_now, total))
    ratio = step_display / max(1, total)
    if active_scenario and total > 0:
        text = f"{active_scenario} · Step {step_display}/{total}"
        if not is_running:
            text += " · idle"
    elif active_scenario:
        text = f"{active_scenario} · running"
    else:
        text = "no active scenario"
    st.progress(min(1.0, max(0.0, ratio)), text=text)
