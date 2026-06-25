"""Translate a :class:`Candidate` into a task envelope for the scheduler.

The optimizer chooses *what* the bot should do; the dispatcher names the
scenario file that knows *how*, and bundles the parameters the scheduler
needs (instance, player, scenario, region for skill slot).

Scenario naming feeds the template resolver in
:mod:`scenarios.template_resolver`:

* ``level_up:bahiti:5→6``               → ``level_up_bahiti``
* ``star_tier_up:bahiti:4→5``           → ``star_tier_up_bahiti``
* ``skill_up:bahiti:expedition.1:0→1``  → ``skill_up_bahiti`` + region hint

These resolve through ``games/wos/heroes/heroes/scenarios/upgrade/{action}_{hero}.yaml``
templates with node ``page.heroes.<hero_id>``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scheduler.queue_payload import build_queue_body, enqueue_sync, queue_key

if TYPE_CHECKING:
    import redis

    from optimizer.types import Candidate


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
        msg = f"candidate {c.id!r} has no hero_id"
        raise ValueError(msg)
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

    Built through :func:`scheduler.queue_payload.build_queue_body` — the same
    canonical payload builder ``RedisQueue.schedule`` uses — so a task pushed
    from here is field-for-field indistinguishable from a scheduler push.
    """
    return build_queue_body(
        task_id=env.task_id,
        player_id=env.player_id,
        task_type=env.task_type,
        priority=env.priority,
        run_at=env.run_at,
        instance_id=env.instance_id,
        region=env.region,
        set_node=env.set_node,
        dsl_scenario=env.dsl_scenario,
    )


def enqueue_envelope(env: TaskEnvelope, client: redis.Redis) -> str:
    """Push a task envelope onto the sync Redis client.

    Routes through :func:`scheduler.queue_payload.enqueue_sync` — the shared
    payload builder + dashboard ``queue`` event the async scheduler uses — so
    the dashboard refreshes on dispatch and the payload can't drift from the
    scheduler's. Dispatch is operator/optimizer-driven and deliberate, so it
    does not dedup (``skip_if_duplicate`` is left off). Returns the queue key it
    was written to.
    """
    enqueue_sync(
        client,
        task_id=env.task_id,
        player_id=env.player_id,
        task_type=env.task_type,
        priority=env.priority,
        run_at=env.run_at,
        instance_id=env.instance_id,
        region=env.region,
        set_node=env.set_node,
        dsl_scenario=env.dsl_scenario,
    )
    return queue_key(env.instance_id)


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
        msg = f"candidate {c.id!r} cannot be dispatched without hero_id"
        raise ValueError(msg)
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
