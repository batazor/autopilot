"""Generic runner for imperative DSL scenario YAML.

The heavy implementation lives in sibling ``dsl_scenario_*_mixin`` modules; this
file composes them and re-exports helpers so tests can monkeypatch
``tasks.dsl_scenario`` names without reaching into internals.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from adb import BotActions, _redis, _require_approval, click_approval_enabled
from analysis.overlay import evaluate_overlay_rules_async
from scenarios.dsl_schema import DEFAULT_SCENARIO_PRIORITY
from tasks.dsl_match_mixin import DslMatchMixin
from tasks.dsl_ocr_mixin import DslOcrMixin
from tasks.dsl_persist_mixin import DslPersistMixin
from tasks.dsl_scenario_execute_mixin import DslScenarioExecuteMixin
from tasks.dsl_scenario_helpers import (
    _COLOR_WORD_ALIASES,
    _COND_SCREEN_RE,
    _COND_TEXT_RE,
    _DSL_STEP_ACTION_KEYS,
    _BreakRepeat,
    _collect_ocr_store_targets,
    _decode_redis_value,
    _dsl_cond_allows_step,
    _dsl_step_summary,
    _enqueue_scenario,
    _eval_instance_text_cond,
    _eval_simple_screen_cond,
    _load_area_json,
    _load_yaml,
    _load_yaml_cached,
    _ocr_store_redis_fields,
    _parse_wait_seconds,
    _read_active_player,
    _read_current_screen,
    _read_instance_state_field,
    _repo_root,
    _step_red_dot_requirement,
    _step_tab_active_requirement,
    _strip_quotes,
)
from tasks.dsl_scenario_inline_mixin import DslScenarioInlineMixin
from tasks.dsl_scenario_preempt_mixin import (
    PREEMPT_MARGIN,
    PREEMPT_MAX_YIELDS,
    PREEMPT_YIELD_COUNT_TTL_SECONDS,
    DslScenarioPreemptMixin,
)

__all__ = [
    "PREEMPT_MARGIN",
    "PREEMPT_MAX_YIELDS",
    "PREEMPT_YIELD_COUNT_TTL_SECONDS",
    "DslScenarioTask",
]


@dataclass
class DslScenarioTask(
    DslPersistMixin,
    DslMatchMixin,
    DslOcrMixin,
    DslScenarioPreemptMixin,
    DslScenarioInlineMixin,
    DslScenarioExecuteMixin,
):
    """Generic runner for imperative DSL scenario YAML.

    This is the bridge that lets us keep scenario logic in YAML, while the worker still executes
    tasks from the Redis queue.
    """

    task_id: str
    player_id: str
    priority: int = DEFAULT_SCENARIO_PRIORITY
    cooldown_seconds: int = 1
    is_cooperative: bool = False
    skip_account_check: bool = field(default=True, init=False)
    redis_client: Any | None = field(default=None, repr=False)
    task_type: str = field(default="dsl_scenario", init=False)

    scenario_key: str = ""
    tap_region: str = ""
    tap_x_pct: float | None = None
    tap_y_pct: float | None = None
    start_step_index: int = 0
    # Rank-time effective_priority from RedisQueue.pop_due. Drives cooperative
    # preemption (ADR 0001 §5): a pending task wins only if it outranks us by
    # PREEMPT_MARGIN. Defaults to ``priority`` when unset (legacy callers).
    effective_priority: int = 0
    # Last `match:` result (best-effort), used to tap at the actual matched location
    # instead of the static region center when `*_search` is involved.
    _last_match_region: str = field(default="", init=False, repr=False)
    _last_match_row: dict[str, Any] | None = field(default=None, init=False, repr=False)
    # Same idea as ``_last_match_row`` but for ``ocr:`` steps — set by
    # ``_ocr_audit_step``, consumed by ``_append_trace_row`` via
    # ``ocr_row=self._last_ocr_row`` so confidence/value/threshold land in
    # the trace right next to the failure status.
    _last_ocr_row: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _last_tap_region_clicked: str = field(default="", init=False, repr=False)
    # Set when ``_tap_region`` ran an implicit auto-match (no explicit ``match:`` in
    # the scenario). Lets ``_point_for_region_action`` use the engine-reported best
    # position even if the score didn't clear ``threshold`` — semantically the user
    # said "this button is here, find it" rather than "verify it's there".
    _implicit_match_for_region: str = field(default="", init=False, repr=False)
    _ocr_client: Any | None = field(default=None, init=False, repr=False)
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]] = field(
        default_factory=dict, init=False, repr=False
    )
    # Snapshot of ``dsl_preempt_gen`` at scenario start; debug UI bumps the counter to exit early.
    _preempt_gen_at_start: int = field(default=0, init=False, repr=False)
    # Short-TTL cache of the preempt outcome so per-inline-step probes inside
    # a while_match body don't each re-read the same Redis key. Tuple shape is
    # ``(instance_id, primed_at_monotonic, preempted_int)`` — see
    # :meth:`DslScenarioPreemptMixin._inline_preempt_if_needed`.
    _preempt_gen_cache: tuple[str, float, int] | None = field(
        default=None, init=False, repr=False
    )
    # Trace timing — seeded by ``DslScenarioExecuteMixin.execute`` so every
    # appended row carries ``t`` and (for terminal top-level rows)
    # ``duration_ms``. ``None`` outside an active scenario; the appender
    # tolerates that and skips the stamp.
    _scenario_started_at: float | None = field(default=None, init=False, repr=False)
    _step_start_times: dict[str, float] | None = field(
        default=None, init=False, repr=False
    )
