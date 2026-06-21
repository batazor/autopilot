"""Scenario progress bar math (navigation vs step completion)."""

from dashboard.scenario_progress_metrics import (
    compute_scenario_progress_metrics,
    format_scenario_progress_label,
)


def test_navigating_at_step_zero_does_not_fill_bar() -> None:
    m = compute_scenario_progress_metrics(
        step_current=0,
        step_total=3,
        is_running=True,
        nav_target="chief_profile",
    )
    assert m["is_navigating"] is True
    assert m["completed_steps"] == 0
    assert m["progress_ratio"] == 0.0
    assert m["highlight_step_index"] == -1


def test_navigating_label_omits_step_one_of_three() -> None:
    label = format_scenario_progress_label(
        scenario_label="Who am I — capture chief profile",
        scenario_key="who_i_am",
        step_current=0,
        step_total=3,
        step_iter=0,
        is_running=True,
        is_navigating=True,
        nav_target="chief_profile",
    )
    assert label == "Who am I — capture chief profile · Navigating → chief_profile"
    assert "Step 1/3" not in label


def test_running_step_one_fills_first_third() -> None:
    m = compute_scenario_progress_metrics(
        step_current=0,
        step_total=3,
        is_running=True,
        nav_target="",
    )
    assert m["completed_steps"] == 1
    assert abs(m["progress_ratio"] - 1 / 3) < 1e-6
