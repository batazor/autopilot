from __future__ import annotations

from enum import StrEnum


class PlayerState(StrEnum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    EXECUTING = "executing"
    RECOVERING = "recovering"
    GAME_CLOSED = "game_closed"
    SWITCHING = "switching"


class InstanceState(StrEnum):
    READY = "ready"
    BUSY = "busy"
    CRASHED = "crashed"
    RESTARTING = "restarting"


PLAYER_TRANSITIONS: list[dict[str, object]] = [
    {"trigger": "start_navigate", "source": PlayerState.IDLE, "dest": PlayerState.NAVIGATING},
    {"trigger": "start_execute", "source": PlayerState.NAVIGATING, "dest": PlayerState.EXECUTING},
    {"trigger": "finish", "source": PlayerState.EXECUTING, "dest": PlayerState.IDLE},
    {"trigger": "switch_account", "source": PlayerState.IDLE, "dest": PlayerState.SWITCHING},
    {"trigger": "switched", "source": PlayerState.SWITCHING, "dest": PlayerState.IDLE},
    {
        "trigger": "recover",
        "source": [
            PlayerState.NAVIGATING,
            PlayerState.EXECUTING,
            PlayerState.SWITCHING,
        ],
        "dest": PlayerState.RECOVERING,
    },
    {"trigger": "recovered", "source": PlayerState.RECOVERING, "dest": PlayerState.IDLE},
    {
        "trigger": "game_closed",
        "source": [
            PlayerState.IDLE,
            PlayerState.NAVIGATING,
            PlayerState.EXECUTING,
            PlayerState.RECOVERING,
        ],
        "dest": PlayerState.GAME_CLOSED,
    },
    {"trigger": "game_opened", "source": PlayerState.GAME_CLOSED, "dest": PlayerState.IDLE},
]

INSTANCE_TRANSITIONS: list[dict[str, object]] = [
    {"trigger": "start_task", "source": InstanceState.READY, "dest": InstanceState.BUSY},
    {"trigger": "task_done", "source": InstanceState.BUSY, "dest": InstanceState.READY},
    {
        "trigger": "crash",
        "source": [InstanceState.READY, InstanceState.BUSY],
        "dest": InstanceState.CRASHED,
    },
    {"trigger": "restart", "source": InstanceState.CRASHED, "dest": InstanceState.RESTARTING},
    {"trigger": "recovered", "source": InstanceState.RESTARTING, "dest": InstanceState.READY},
]
