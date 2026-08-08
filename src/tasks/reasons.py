"""Registry of task-outcome reasons.

``TaskResult.metadata["reason"]`` is the single string an operator sees when they
ask why a task ended the way it did — in ``botctl history``, in the queue view,
and as a metric label. It was ~50 ad-hoc literals scattered across the DSL
executors and the worker, with no list anywhere, so:

* a typo produced a brand-new metric series that looked like a real reason;
* nothing could answer "was this a config bug or a device blip" without matching
  strings by hand;
* ``config.telemetry.safe_reason_label`` could only filter by *shape*, because
  there was no set of known names to check against.

**Every member's value is exactly the string the code already emitted.** That is
a hard constraint, not a coincidence: the coordinator's circuit breaker compares
reasons by equality and *persists* them (``last_reason`` /
``same_reason_streak`` live in Redis), so changing a value would be a
stored-state migration. Dozens of tests also assert the exact strings.

Scope is the *task outcome* vocabulary produced under ``src/tasks`` and
``src/worker``. Two neighbouring vocabularies are deliberately NOT here:

* dashboard events (``publish_dashboard_event(reason="running"|"finished"|…)``)
  — a UI notification channel that happens to reuse the word "reason";
* overlay match-row diagnostics (``red_dot_missing``, ``score_below_threshold``,
  …) produced by the overlay engine and nested inside a match row.

Conflating them is how a registry stops meaning anything.

``tests/tasks/test_reason_registry.py`` scans the source for reason literals and
fails if one is not a member here — the registry is only worth having because
that test exists.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCategory(StrEnum):
    """What KIND of thing went wrong — the question `botctl history` should
    answer without the reader matching strings by hand."""

    NAVIGATION = "navigation"
    """Routing to the scenario's node failed."""

    PERCEPTION = "perception"
    """The bot looked and did not see, or could not read, what it needed."""

    APPROVAL = "approval"
    """An operator gate blocked the action (click-approval mode)."""

    CONFIG = "config"
    """A YAML / labelling defect. A human has to edit a file; retrying cannot help."""

    SCENARIO = "scenario"
    """The scenario itself decided not to act — a guard, a cond, a TTL backoff.
    Not a failure; these are the rows that should NOT read as red."""

    IDENTITY = "identity"
    """Player or screen identity is not resolved yet; the task waits its turn."""

    PREEMPTED = "preempted"
    """Yielded to higher-priority work. Expected, and rescheduled."""

    LIFECYCLE = "lifecycle"
    """Worker/process lifecycle — restart, stuck-task abort, dead process."""

    DEVICE = "device"
    """The device or ADB refused the action."""

    INFRA = "infra"
    """Our own bug: a handler raised, or a dispatcher could not dispatch."""


class TaskReason(StrEnum):
    """Known task-outcome reasons. Values are frozen — see the module docstring."""

    # --- navigation -------------------------------------------------------
    NAVIGATION_FAILED = "navigation_failed"
    OFF_NODE_NAVIGATE_DISABLED = "off_node_navigate_disabled"

    # --- perception -------------------------------------------------------
    CAPTURE_FAILED = "capture_failed"
    DETECT_FAILED = "detect_failed"
    OCR_FAILED = "ocr_failed"
    MATCH_REGION_NOT_FOUND = "match_region_not_found"
    MATCH_GUARD_FAILED = "match_guard_failed"
    WAIT_SCREEN_TIMEOUT = "wait_screen_timeout"
    WHILE_MATCH_NO_ITERATIONS = "while_match_no_iterations"
    NO_POPUP = "no_popup"
    SCREEN_PAGE = "screen_page"
    NO_SAFE_TAP = "no_safe_tap"
    CAPTCHA = "captcha"
    MAX_TAPS = "max_taps"
    MAX_LAYERS = "max_layers"

    # --- approval ---------------------------------------------------------
    TAP_NOT_APPROVED = "tap_not_approved"
    SWIPE_NOT_APPROVED = "swipe_not_approved"
    LONG_CLICK_NOT_APPROVED = "long_click_not_approved"
    TYPE_TEXT_NOT_APPROVED = "type_text_not_approved"
    SYSTEM_BACK_NOT_APPROVED = "system_back_not_approved"
    WHILE_MATCH_NO_ITERATIONS_NOT_APPROVED = "while_match_no_iterations_not_approved"
    TAP_REJECTED = "tap_rejected"
    PURCHASE_GUARD = "purchase_guard"

    # --- config -----------------------------------------------------------
    UNKNOWN_REGION = "unknown_region"
    REGION_NOT_FOUND = "region_not_found"
    MISSING_BBOX = "missing_bbox"
    BBOX_MISSING = "bbox_missing"
    INVALID_BBOX = "invalid_bbox"
    DELAY_UNRESOLVED = "delay_unresolved"
    UNKNOWN_STEP_KEY = "unknown_step_key"
    UNSUPPORTED = "unsupported"
    INVALID_STEPS = "invalid_steps"
    SCENARIO_INVALID = "scenario_invalid"
    SCENARIO_NOT_FOUND = "scenario_not_found"
    MISSING_SCENARIO_KEY = "missing_scenario_key"
    UNKNOWN_EXEC = "unknown_exec"

    # --- scenario control flow (benign) -----------------------------------
    SCENARIO_COND_FALSE = "scenario_cond_false"
    COND_FALSE = "cond_false"
    TTL = "ttl"
    TTL_EXIT = "ttl_exit"
    WAIT_SCREEN_TIMEOUT_OPTIONAL = "wait_screen_timeout_optional"
    BREAK_OUTSIDE_LOOP = "break_outside_loop"
    ELSE_STOP = "else_stop"

    # --- identity ---------------------------------------------------------
    AWAITING_PLAYER_IDENTITY = "awaiting_player_identity"
    AWAITING_SCREEN_IDENTITY = "awaiting_screen_identity"
    IDENTITY_NOT_RESOLVED = "identity_not_resolved"

    # --- preempted --------------------------------------------------------
    PREEMPTED_BY_HIGHER_PRIORITY = "preempted_by_higher_priority"
    PREEMPTED_BY_DEVICE_LEVEL = "preempted_by_device_level"
    DSL_PREEMPTED_DEBUG = "dsl_preempted_debug"

    # --- lifecycle --------------------------------------------------------
    WORKER_RESTART = "worker_restart"
    ABORTED_FOR_RESTART = "aborted_for_restart"
    ABORTED_STUCK = "aborted_stuck"
    PROCESS_DEAD_AFTER_RETRIES = "process_dead_after_retries"

    # --- device -----------------------------------------------------------
    TAP_FAILED = "tap_failed"

    # --- infra ------------------------------------------------------------
    EXEC_FAILED = "exec_failed"
    EXEC_REPORTED_FAILURE = "exec_reported_failure"


# Deliberately a side table, not a member attribute: a StrEnum member's value
# must stay the bare string so `str(reason)` keeps working at every Redis and
# JSON boundary.
#
# Categories are ASSIGNED here, never inferred from the name. Suffixes lie:
# `wait_screen_timeout_optional` is benign and `no_popup` is a perception miss
# that several handlers treat as success. `tasks/dsl_scenario_helpers` records
# the same decision for the exec-failure contract.
REASON_CATEGORY: dict[TaskReason, ReasonCategory] = {
    TaskReason.NAVIGATION_FAILED: ReasonCategory.NAVIGATION,
    TaskReason.OFF_NODE_NAVIGATE_DISABLED: ReasonCategory.NAVIGATION,
    TaskReason.CAPTURE_FAILED: ReasonCategory.PERCEPTION,
    TaskReason.DETECT_FAILED: ReasonCategory.PERCEPTION,
    TaskReason.OCR_FAILED: ReasonCategory.PERCEPTION,
    TaskReason.MATCH_REGION_NOT_FOUND: ReasonCategory.PERCEPTION,
    TaskReason.MATCH_GUARD_FAILED: ReasonCategory.PERCEPTION,
    TaskReason.WAIT_SCREEN_TIMEOUT: ReasonCategory.PERCEPTION,
    TaskReason.WHILE_MATCH_NO_ITERATIONS: ReasonCategory.PERCEPTION,
    TaskReason.NO_POPUP: ReasonCategory.PERCEPTION,
    TaskReason.SCREEN_PAGE: ReasonCategory.PERCEPTION,
    TaskReason.NO_SAFE_TAP: ReasonCategory.PERCEPTION,
    TaskReason.CAPTCHA: ReasonCategory.PERCEPTION,
    TaskReason.MAX_TAPS: ReasonCategory.PERCEPTION,
    TaskReason.MAX_LAYERS: ReasonCategory.PERCEPTION,
    TaskReason.TAP_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.SWIPE_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.LONG_CLICK_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.TYPE_TEXT_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.SYSTEM_BACK_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.WHILE_MATCH_NO_ITERATIONS_NOT_APPROVED: ReasonCategory.APPROVAL,
    TaskReason.TAP_REJECTED: ReasonCategory.APPROVAL,
    TaskReason.PURCHASE_GUARD: ReasonCategory.APPROVAL,
    TaskReason.UNKNOWN_REGION: ReasonCategory.CONFIG,
    TaskReason.REGION_NOT_FOUND: ReasonCategory.CONFIG,
    TaskReason.MISSING_BBOX: ReasonCategory.CONFIG,
    TaskReason.BBOX_MISSING: ReasonCategory.CONFIG,
    TaskReason.INVALID_BBOX: ReasonCategory.CONFIG,
    TaskReason.DELAY_UNRESOLVED: ReasonCategory.CONFIG,
    TaskReason.UNKNOWN_STEP_KEY: ReasonCategory.CONFIG,
    TaskReason.UNSUPPORTED: ReasonCategory.CONFIG,
    TaskReason.INVALID_STEPS: ReasonCategory.CONFIG,
    TaskReason.SCENARIO_INVALID: ReasonCategory.CONFIG,
    TaskReason.SCENARIO_NOT_FOUND: ReasonCategory.CONFIG,
    TaskReason.MISSING_SCENARIO_KEY: ReasonCategory.CONFIG,
    TaskReason.UNKNOWN_EXEC: ReasonCategory.CONFIG,
    TaskReason.SCENARIO_COND_FALSE: ReasonCategory.SCENARIO,
    TaskReason.COND_FALSE: ReasonCategory.SCENARIO,
    TaskReason.TTL: ReasonCategory.SCENARIO,
    TaskReason.TTL_EXIT: ReasonCategory.SCENARIO,
    TaskReason.WAIT_SCREEN_TIMEOUT_OPTIONAL: ReasonCategory.SCENARIO,
    TaskReason.BREAK_OUTSIDE_LOOP: ReasonCategory.SCENARIO,
    TaskReason.ELSE_STOP: ReasonCategory.SCENARIO,
    TaskReason.AWAITING_PLAYER_IDENTITY: ReasonCategory.IDENTITY,
    TaskReason.AWAITING_SCREEN_IDENTITY: ReasonCategory.IDENTITY,
    TaskReason.IDENTITY_NOT_RESOLVED: ReasonCategory.IDENTITY,
    TaskReason.PREEMPTED_BY_HIGHER_PRIORITY: ReasonCategory.PREEMPTED,
    TaskReason.PREEMPTED_BY_DEVICE_LEVEL: ReasonCategory.PREEMPTED,
    TaskReason.DSL_PREEMPTED_DEBUG: ReasonCategory.PREEMPTED,
    TaskReason.WORKER_RESTART: ReasonCategory.LIFECYCLE,
    TaskReason.ABORTED_FOR_RESTART: ReasonCategory.LIFECYCLE,
    TaskReason.ABORTED_STUCK: ReasonCategory.LIFECYCLE,
    TaskReason.PROCESS_DEAD_AFTER_RETRIES: ReasonCategory.LIFECYCLE,
    TaskReason.TAP_FAILED: ReasonCategory.DEVICE,
    TaskReason.EXEC_FAILED: ReasonCategory.INFRA,
    TaskReason.EXEC_REPORTED_FAILURE: ReasonCategory.INFRA,
}

_VALUES: frozenset[str] = frozenset(r.value for r in TaskReason)


def is_known_reason(reason: str) -> bool:
    """Whether ``reason`` is a registered task-outcome reason."""
    return (reason or "").strip() in _VALUES


def reason_category(reason: str) -> str:
    """Category of ``reason``; ``""`` when unknown or empty.

    Returns a plain ``str`` so callers can drop it straight into a Redis hash or
    a metric label without thinking about enum serialisation.
    """
    raw = (reason or "").strip()
    if raw not in _VALUES:
        return ""
    return str(REASON_CATEGORY.get(TaskReason(raw), ""))
