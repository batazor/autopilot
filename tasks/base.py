from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class TaskResult:
    success: bool
    next_run_at: datetime
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
