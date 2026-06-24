"""Focus-mode orchestration — point-wise scenario launch.

Generalises the fish-detect Play flow into a reusable primitive: pin an instance
to a single scenario, make sure **exactly one** worker is alive to run it, and
enqueue it. Used by the ``/api/dev/bot/instance/{id}/focus`` endpoints, ``botctl
run --focus`` and the /run launcher.

A worker is considered alive if either an isolated ``instance_runner`` process
exists *or* the instance heartbeats a fresh ``last_seen_at`` (a supervisor-spawned
worker) — so focus retasks the existing worker instead of spawning a second one
on the same device. The worker honours the ``focus_scenario`` Redis flag each
tick (see :mod:`worker.focus_mode`).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from config.paths import repo_root
from dsl import template_resolver as _tmpl
from worker import focus_mode, local_bot

if TYPE_CHECKING:
    import redis

# A worker rewrites ``last_seen_at`` every tick; treat a heartbeat within this
# window as "a worker is alive for this instance" (covers supervisor workers,
# which have no isolated instance_runner process to detect).
_WORKER_HEARTBEAT_FRESH_S = 30.0


def _heartbeat_alive(client: redis.Redis, instance_id: str) -> bool:
    try:
        raw = client.hget(f"wos:instance:{instance_id}:state", "last_seen_at")
    except Exception:
        return False
    if not raw:
        return False
    try:
        last = float(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) <= _WORKER_HEARTBEAT_FRESH_S


def worker_alive(client: redis.Redis, instance_id: str) -> bool:
    """True if any worker (isolated or supervisor) is running this instance."""
    if local_bot.instance_worker_status(instance_id).get("running"):
        return True
    return _heartbeat_alive(client, instance_id)


def _resolve_scenario(scenario_key: str) -> bool:
    """Validate the scenario exists; return whether it is device-level.

    Raises ``KeyError`` for an unknown scenario (mirrors ``enqueue_user_task``).
    """
    resolved = _tmpl.resolve(repo_root(), scenario_key)
    if resolved is None:
        msg = f"unknown scenario: {scenario_key}"
        raise KeyError(msg)
    doc = _tmpl.load_doc(repo_root(), scenario_key)
    return bool(doc and doc[1].get("device_level") is True)


def focus_instance(
    client: redis.Redis,
    *,
    instance_id: str,
    scenario_key: str,
    player_id: str = "",
    abort_running: bool = False,
) -> dict[str, Any]:
    """Pin ``instance_id`` to ``scenario_key``, ensure a worker, and enqueue it.

    Returns ``{ok, instance_id, scenario, focus, task_id, queue_key, replaced,
    worker_started}``.
    """
    from api.services.queue_api import enqueue_user_task

    instance_id = (instance_id or "").strip()
    scenario_key = (scenario_key or "").strip()
    player_id = (player_id or "").strip()
    if not instance_id:
        msg = "instance_id required"
        raise ValueError(msg)
    if not scenario_key:
        msg = "scenario_key required"
        raise ValueError(msg)

    device_level = _resolve_scenario(scenario_key)  # raises KeyError if unknown
    if not device_level and not player_id:
        msg = "player_id required for account-level scenarios"
        raise ValueError(msg)

    # Pin focus BEFORE enqueue/worker-start so a freshly-started worker reads it
    # on its first tick and never seeds check_main_city / who_i_am.
    focus_mode.set_focus(
        client, instance_id, scenario=scenario_key, player=player_id
    )

    worker_started = False
    if not worker_alive(client, instance_id):
        local_bot.start_instance_worker(instance_id)
        worker_started = True

    result = enqueue_user_task(
        client,
        scenario_key=scenario_key,
        instance_id=instance_id,
        player_id=player_id,
        scheduled_at=time.time(),
        priority=90_000,
        replace_existing=True,
        abort_running=abort_running,
    )
    return {
        "ok": True,
        "instance_id": instance_id,
        "scenario": scenario_key,
        "focus": scenario_key,
        "worker_started": worker_started,
        **result,
    }


def unfocus_instance(
    client: redis.Redis,
    *,
    instance_id: str,
    stop_worker: bool = True,
) -> dict[str, Any]:
    """Clear focus mode; optionally stop the isolated worker.

    Per the design, the dashboard "Stop" both clears focus and stops the
    isolated worker. A supervisor-spawned worker is left running (clearing the
    flag returns it to normal autopilot).
    """
    instance_id = (instance_id or "").strip()
    if not instance_id:
        msg = "instance_id required"
        raise ValueError(msg)
    focus_mode.clear_focus(client, instance_id)
    worker = {"running": False, "instance_id": instance_id, "pid": None}
    if stop_worker:
        worker = local_bot.stop_instance_worker(instance_id)
    return {"ok": True, "instance_id": instance_id, "focus": "", "worker": worker}


def focus_status(client: redis.Redis, instance_id: str) -> dict[str, Any]:
    """Current focus + worker liveness for an instance (for status endpoints)."""
    scenario, player = focus_mode.read_focus(client, instance_id)
    return {
        "instance_id": instance_id,
        "running": worker_alive(client, instance_id),
        "focus_scenario": scenario,
        "focus_player": player,
    }
