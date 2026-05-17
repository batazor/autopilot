from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class TaskResult:
    success: bool
    """When ``None``, the worker does not re-queue this task (one-shot completion)."""

    next_run_at: datetime | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class BaseTask(Protocol):
    task_id: str
    player_id: str
    task_type: str
    priority: int
    cooldown_seconds: int
    is_cooperative: bool

    async def execute(self, instance_id: str) -> TaskResult: ...

    def estimate_duration(self) -> int: ...
