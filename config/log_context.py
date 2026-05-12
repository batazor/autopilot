"""Contextvar-backed log enrichment: device id, player id, current FSM node.

Multiple ``InstanceWorker`` instances run as concurrent asyncio tasks in the
same process (see ``worker/async_supervisor.py``). ``ContextVar`` propagates
per ``asyncio.Task``, so each worker sees its own values without explicit
threading through call sites.

Set at boundaries the worker knows things change:
- per-worker boot — ``inst``
- per-task execute — ``player``
- per-tick screen detect — ``node`` (and ``player`` from Redis state)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

_inst: ContextVar[str] = ContextVar("wos_log_inst", default="")
_player: ContextVar[str] = ContextVar("wos_log_player", default="")
_node: ContextVar[str] = ContextVar("wos_log_node", default="")


def set_log_context(
    *,
    inst: str | None = None,
    player: str | None = None,
    node: str | None = None,
) -> None:
    """Update one or more context values. ``None`` leaves the var unchanged."""
    if inst is not None:
        _inst.set(str(inst))
    if player is not None:
        _player.set(str(player))
    if node is not None:
        _node.set(str(node))


class LogContextFilter(logging.Filter):
    """Attach ``inst`` / ``player`` / ``node`` attrs to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.inst = _inst.get() or "-"
        record.player = _player.get() or "-"
        record.node = _node.get() or "-"
        return True
