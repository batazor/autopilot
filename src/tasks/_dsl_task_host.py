"""Type-check-only host Protocol for the ``DslScenarioTask`` mixin family.

The runner is composed of six mixin classes
(``DslPersistMixin``, ``DslMatchMixin``, ``DslOcrMixin``,
``DslScenarioPreemptMixin``, ``DslScenarioInlineMixin``,
``DslScenarioExecuteMixin``) and a host ``DslScenarioTask`` dataclass that
supplies the shared attributes (``redis_client``, ``scenario_key``,
``_last_match_row`` …) and orchestrates the call graph.

Each mixin freely calls into siblings and reads host attrs (e.g.
``self.redis_client``), but from a single-mixin perspective those names are
unresolved — only the host class brings everything together. ``ty`` flags
~140 ``unresolved-attribute`` errors per file because of this.

This module declares one ``Protocol`` listing every attribute and method
that crosses a mixin boundary. Each mixin inherits from this Protocol
**only under** ``TYPE_CHECKING`` (runtime base stays ``object``, so the
MRO of the composed ``DslScenarioTask`` class is unchanged):

    if TYPE_CHECKING:
        from tasks._dsl_task_host import _DslTaskHost as _Base
    else:
        _Base = object

    class DslScenarioExecuteMixin(_Base):
        ...

A ``Protocol`` is used rather than a regular base class so empty method
bodies (``...``) are accepted, and so the conditional "base" doesn't
collide with the other mixin bases in the MRO of ``DslScenarioTask``.
"""
from __future__ import annotations

from typing import Any, Protocol


class _DslTaskHost(Protocol):
    # ------------------------------------------------------------------
    # Host-class attributes (declared on ``DslScenarioTask``)
    # ------------------------------------------------------------------
    task_id: str
    task_type: str
    player_id: str
    priority: int
    effective_priority: int
    redis_client: Any | None

    scenario_key: str
    tap_region: str
    tap_x_pct: float | None
    tap_y_pct: float | None
    start_step_index: int

    _last_match_region: str
    _last_match_row: dict[str, Any] | None
    _last_ocr_row: dict[str, Any] | None
    _last_tap_region_clicked: str
    _implicit_match_for_region: str

    _ocr_client: Any | None
    _exclude_match_top_lefts: dict[str, list[tuple[int, int]]]

    _preempt_gen_at_start: int
    _preempt_gen_cache: tuple[str, float, int] | None

    _scenario_started_at: float | None
    _step_start_times: dict[str, float] | None
    _steps_trace: list[dict[str, Any]]

    # ------------------------------------------------------------------
    # Cross-mixin helpers (concrete implementation lives in one mixin,
    # but every other mixin calls into it via ``self.<name>(...)``)
    # ------------------------------------------------------------------

    # Cross-mixin method stubs. Signatures are deliberately loose
    # (``*args, **kwargs`` / ``Any``) — Protocol overrides interact badly
    # with positional-vs-keyword variance and ``@staticmethod`` in mixin
    # implementations, and the goal here is only to give ``ty`` knowledge
    # that the names *exist*, not to re-check their per-arg types. Each
    # mixin still has its own concrete signature on the actual method.

    # DslPersistMixin
    def _state_flat(self) -> Any: ...
    def _append_trace_row(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _reset_dsl_audit_state(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _clear_step_context(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _write_step_context(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _persist_dsl_last_match(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _persist_dsl_last_color(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _persist_dsl_last_ocr(self, *args: Any, **kwargs: Any) -> Any: ...

    # DslMatchMixin
    async def _match_region(self, *args: Any, **kwargs: Any) -> Any: ...

    # DslOcrMixin
    async def _ocr_region(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _ocr_region_bulk(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _ocr_audit_step(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _persist_ocr_result(self, *args: Any, **kwargs: Any) -> Any: ...
    def _get_ocr_client(self) -> Any: ...

    # DslScenarioPreemptMixin
    async def _read_dsl_preempt_gen(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _preempted_by_new_debug(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _preempted_by_higher_priority(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _inline_preempt_if_needed(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _read_yield_count(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _bump_yield_count(self, *args: Any, **kwargs: Any) -> Any: ...

    # DslScenarioInlineMixin
    async def _run_inline_step(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _tap_region(self, *args: Any, **kwargs: Any) -> Any: ...
    def _point_for_region_action(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _run_exec_step(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _run_system_back_step(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _navigate_to_node(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _pause_for_while_match_no_iterations_approval(
        self, *args: Any, **kwargs: Any
    ) -> Any: ...
