"""Translate a :class:`Candidate` into a task envelope for the scheduler.

The optimizer chooses *what* the bot should do; the dispatcher names the
scenario file that knows *how*, and bundles the parameters the scheduler
needs (instance, player, scenario, region for skill slot).

Scenario naming matches :mod:`cmd.generate_upgrade_scenarios`:

* ``level_up:bahiti:5→6``               → ``level_up_bahiti``
* ``star_tier_up:bahiti:4→5``           → ``star_tier_up_bahiti``
* ``skill_up:bahiti:expedition.1:0→1``  → ``skill_up_bahiti`` + region hint

These map 1:1 to ``scenarios/heroes/upgrade/<scenario>.yaml`` with node
``page.heroes.<hero_id>``.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from optimizer.types import Candidate

if TYPE_CHECKING:
    import redis


@dataclass(frozen=True)
class TaskEnvelope:
    """The minimum field set needed to push a scenario into ``RedisQueue``.

    ``task_id`` is unique-per-enqueue, ``task_type`` selects the DSL
    runner, ``dsl_scenario`` names the scenario YAML file (without
    extension), ``region`` (when set) tells skill-up which slot to tap,
    and ``set_node`` pins the screen the bot must be on first.
    """

    task_id: str
    task_type: str
    player_id: str
    instance_id: str
    dsl_scenario: str
    set_node: str
    region: str | None = None
    priority: int = 50_000
    run_at: float = 0.0
    """``run_at <= now`` → run as soon as the queue picks it up."""


def scenario_name_for(c: Candidate) -> str:
    """Return the scenario filename (no extension) the bot should run."""
    if not c.hero_id:
        raise ValueError(f"candidate {c.id!r} has no hero_id")
    return f"{c.action}_{c.hero_id}"


def region_for(c: Candidate) -> str | None:
    """Skill-slot region for ``skill_up`` candidates; ``None`` otherwise."""
    if c.action != "skill_up":
        return None
    payload = c.payload or {}
    slot = payload.get("slot")
    if slot is None:
        return None
    return f"page.heroes.unit.skill_{int(slot)}"


def envelope_to_redis_payload(env: TaskEnvelope) -> dict[str, object]:
    """Return the JSON-ready body the scheduler reads off the queue.

    Mirrors ``scheduler.queue.RedisQueue.schedule`` field-for-field so a
    task pushed from here is indistinguishable from one the runner pushed.
    """
    body: dict[str, object] = {
        "task_id": env.task_id,
        "player_id": env.player_id,
        "task_type": env.task_type,
        "priority": env.priority,
        "run_at": env.run_at,
        "instance_id": env.instance_id,
        "created_at": time.time(),
    }
    if env.region:
        body["region"] = env.region
    if env.set_node:
        body["set_node"] = env.set_node
    if env.dsl_scenario:
        body["dsl_scenario"] = env.dsl_scenario
    return body


def queue_key(instance_id: str) -> str:
    """Same convention as ``scheduler.queue._queue_key``."""
    iid = (instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


def enqueue_envelope(env: TaskEnvelope, client: redis.Redis) -> str:
    """Push a task envelope onto the sync Redis client.

    Uses ``ZADD`` with the run-at timestamp as score (the same primitive
    the async scheduler uses) so the bot's worker picks it up via the
    normal ``pop_due`` path. Returns the queue key it was written to.
    """
    body = envelope_to_redis_payload(env)
    payload = json.dumps(body)
    qk = queue_key(env.instance_id)
    client.zadd(qk, {payload: env.run_at})
    return qk


def build_envelope(
    c: Candidate,
    *,
    player_id: str,
    instance_id: str,
    priority: int = 50_000,
    now: float | None = None,
) -> TaskEnvelope:
    """Materialise a :class:`TaskEnvelope` for ``c``.

    ``task_type=dsl_scenario`` matches the worker dispatch path that
    looks for ``dsl_scenario`` in the queue payload and runs the named
    YAML's ``steps``. ``set_node`` ensures the BFS navigator reaches the
    hero's screen before the steps fire.
    """
    if c.hero_id is None:
        raise ValueError(f"candidate {c.id!r} cannot be dispatched without hero_id")
    return TaskEnvelope(
        task_id=f"optimizer:{uuid.uuid4().hex[:12]}",
        task_type="dsl_scenario",
        player_id=str(player_id),
        instance_id=str(instance_id),
        dsl_scenario=scenario_name_for(c),
        set_node=f"page.heroes.{c.hero_id}",
        region=region_for(c),
        priority=priority,
        run_at=float(now if now is not None else time.time()),
    )
