"""Type-check-only host Protocol for the ``InstanceWorker`` mixin family.

``InstanceWorker`` is composed of nine mixins (UI / Overlay / Tasks /
Blocking / Redis / Health / ScreenDetect / Screen / Rolling). Each mixin
freely calls into siblings (``await self._set_instance_state(...)``,
``await self._run_blocking(...)``) and reads the host's attributes
(``self._cfg``, ``self._redis``, ``self._bot_actions`` …). From a
single-mixin perspective those names are unresolved.

Mirror of ``tasks/_dsl_task_host.py``: each mixin "inherits" from this
Protocol under ``TYPE_CHECKING`` only, so the runtime MRO of
``InstanceWorker`` is unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import asyncio

    import redis.asyncio as aioredis


class _InstanceWorkerHost(Protocol):
    # ------------------------------------------------------------------
    # Host-class attributes (set by ``InstanceWorker.__init__``)
    # ------------------------------------------------------------------
    _cfg: Any
    _settings: Any
    _redis: aioredis.Redis | None  # type: ignore[type-arg]
    _queue: Any | None
    _owns_redis: bool
    _claims: Any | None
    _bot_actions: Any
    _ocr_client: Any
    _screen_detector: Any
    _instance_state: Any
    _ui_paused: bool
    _startup_pause_reason: str
    _task_busy: asyncio.Event
    _rolling_snap_seq: int
    _last_current_screen: str | None
    _last_detected_screen: str | None
    _last_detected_screen_at: float
    _unknown_since: float
    _screen_unknown_streak: int
    _overlay_rule_eval_state_by_player: dict[str, dict[str, float]]
    _blocking_pool: Any
    _rolling_snapshot_task: asyncio.Task[None] | None
    _abort_task_listener_task: asyncio.Task[None] | None
    _blocking_executor_live: bool
    _stopping: bool
    _task_registry: Any
    _current_task_handle: asyncio.Task[Any] | None
    _task_aborted_for_restart: bool
    _task_abort_result_reason: str

    # ------------------------------------------------------------------
    # Cross-mixin methods. Loose ``*args, **kwargs`` signatures so ty
    # doesn't flag concrete impls as ``invalid-method-override``.
    # ------------------------------------------------------------------
    async def _set_instance_state(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _ensure_account(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _execute_task(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _drain_ui_commands(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _handle_failure(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _restart_instance(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _maybe_enqueue_who_i_am_when_active_player_missing(
        self, *args: Any, **kwargs: Any
    ) -> Any: ...
    async def _run_blocking(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _cancel_current_task(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _read_active_player(self, *args: Any, **kwargs: Any) -> Any: ...
