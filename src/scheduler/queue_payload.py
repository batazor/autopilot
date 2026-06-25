"""Single source of truth for the worker-queue payload, dedup, and enqueue.

Three producers push tasks onto ``wos:queue:<instance>``:

* the async scheduler — :meth:`scheduler.queue.RedisQueue.schedule`
* the notify publisher — :meth:`modules.notify.publisher.RedisPublisher.enqueue_scenario`
* the optimizer dispatcher — :func:`optimizer.dispatcher.enqueue_envelope`

The first is async; the other two hold a *sync* redis client. They used to
each hand-build a JSON body and raw-``ZADD`` it, so three things drifted apart:
the payload field set, the atomic dedup-then-ZADD, and the dashboard
``queue/enqueue`` event that refreshes the UI. This module is the shared seam:

* :func:`build_queue_body` — the canonical payload dict (every field the worker
  reads off the queue). The async ``schedule`` and the sync facade both build
  through it, so the shape can no longer diverge.
* :data:`DEDUP_ZADD_LUA` + :func:`dedup_zadd_args` — the atomic
  dedup-then-ZADD primitive, shared by ``schedule`` (EVALSHA via
  ``register_script``) and :func:`enqueue_sync` (sync ``EVAL``).
* :func:`enqueue_sync` — the sync mirror of ``schedule``: build body → optional
  Lua dedup (else raw ZADD) → publish the dashboard event.

Dedup keys on the *effective* task type (:func:`effective_task_type`): cron and
overlay pushes carry the scenario key in ``task_type``; notify/optimizer pushes
carry it in ``dsl_scenario`` under a generic ``task_type="dsl_scenario"``. Both
the Lua (stored side) and :func:`dedup_zadd_args` (want side) resolve the
effective key so dedup is correct regardless of which producer wrote the item.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


def queue_key(instance_id: str) -> str:
    """Per-instance queue ZSET key. The one definition of this convention."""
    iid = str(instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


def effective_task_type(task_type: Any, dsl_scenario: Any = None) -> str:
    """Scenario key to use for dedup / gating / ranking lookups.

    Cron- and overlay-pushed scenarios set ``task_type`` to the scenario key.
    Notify and optimizer pushes set ``task_type="dsl_scenario"`` with the real
    key in ``dsl_scenario`` (the generic worker-dispatch shape). Resolve the
    field-carried key for those so they are treated like their cron equivalents.
    """
    tt = str(task_type or "").strip()
    if tt == "dsl_scenario":
        ds = str(dsl_scenario or "").strip()
        if ds:
            return ds
    return tt


# Atomic dedup-then-ZADD. Two producers (e.g. the rolling overlay tick and the
# after-task overlay tick) used to both pass a Python ``has_pending_duplicate``
# guard and both ZADD the same logical scenario, since the guard read the ZSET
# in one round-trip and wrote in another. Moving the scan + write into a single
# ``EVAL`` makes Redis the serialization point.
#
# Match semantics (same predicate as ``RedisQueue.has_pending_duplicate``):
# * always filter ``(instance_id, effective_task_type)``;
# * region filters unless ``ignore_region == "1"``;
# * player-bound enqueue (``player_id != ""``) matches in-flight items whose
#   ``data.player_id`` is either ``""`` (device-level) or the same player — so a
#   queued cross-player item for the same task_type doesn't block;
# * device-level enqueue (``player_id == ""``) matches any player — one queued
#   device-level item blocks re-pushing for everyone.
#
# The stored side resolves the effective task type (``dsl_scenario`` field under
# ``task_type="dsl_scenario"``) so a notify/optimizer push dedups against a cron
# push of the same scenario, and two notify pushes of *different* scenarios are
# not collapsed just because they share ``task_type="dsl_scenario"``.
#
# Returns 1 if ZADD was applied, 0 if a duplicate was found and ZADD skipped.
DEDUP_ZADD_LUA = """
local function s(v)
    if v == nil or v == cjson.null then return "" end
    return tostring(v)
end

local function eff(data)
    local tt = s(data.task_type)
    if tt == "dsl_scenario" then
        local ds = s(data.dsl_scenario)
        if ds ~= "" then return ds end
    end
    return tt
end

local task_type = ARGV[3]
local player_id = ARGV[4]
local instance_id = ARGV[5]
local want_region = ARGV[6]
local ignore_region = ARGV[7] == "1"
local device_level_enqueue = player_id == ""

local items = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", "+inf")
for i = 1, #items do
    local ok, data = pcall(cjson.decode, items[i])
    if ok and type(data) == "table" then
        if s(data.instance_id) == instance_id
           and eff(data) == task_type then
            local data_pid = s(data.player_id)
            if device_level_enqueue or data_pid == "" or data_pid == player_id then
                if ignore_region or s(data.region) == want_region then
                    return 0
                end
            end
        end
    end
end

redis.call("ZADD", KEYS[1], ARGV[2], ARGV[1])
return 1
"""


def build_queue_body(
    *,
    task_id: str,
    player_id: str,
    task_type: str,
    priority: int,
    run_at: float,
    instance_id: str,
    region: str | None = None,
    tap_x_pct: float | None = None,
    tap_y_pct: float | None = None,
    threshold: float | None = None,
    score: float | None = None,
    set_node: str | None = None,
    dsl_scenario: str | None = None,
    args: dict[str, Any] | None = None,
    match_top_left_x: int | None = None,
    match_top_left_y: int | None = None,
    template_w: int | None = None,
    template_h: int | None = None,
    tap_match_x_pct: float | None = None,
    tap_match_y_pct: float | None = None,
    start_step_index: int = 0,
    expires_at: float | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    """Build the canonical queue payload dict.

    The one place that decides which fields land on a queue item and how they
    are coerced. ``created_at`` defaults to ``time.time()`` (the stable
    tie-breaker used by ranking) when not supplied.
    """
    body: dict[str, Any] = {
        "task_id": task_id,
        "player_id": player_id,
        "task_type": task_type,
        "priority": priority,
        "run_at": run_at,
        "instance_id": instance_id,
    }
    if region is not None and str(region).strip() != "":
        body["region"] = str(region).strip()
    if tap_x_pct is not None:
        body["tap_x_pct"] = float(tap_x_pct)
    if tap_y_pct is not None:
        body["tap_y_pct"] = float(tap_y_pct)
    if threshold is not None:
        body["threshold"] = float(threshold)
    if score is not None:
        fs = float(score)
        body["score"] = fs
        # Alias: some payloads/tools only preserved one key; pop_due prefers this first.
        body["overlay_match_score"] = fs
    if set_node is not None and str(set_node).strip() != "":
        body["set_node"] = str(set_node).strip()
    if dsl_scenario is not None and str(dsl_scenario).strip() != "":
        body["dsl_scenario"] = str(dsl_scenario).strip()
    if isinstance(args, dict) and args:
        body["args"] = json.loads(json.dumps(args))
    if match_top_left_x is not None:
        body["match_top_left_x"] = int(match_top_left_x)
    if match_top_left_y is not None:
        body["match_top_left_y"] = int(match_top_left_y)
    if template_w is not None:
        body["template_w"] = int(template_w)
    if template_h is not None:
        body["template_h"] = int(template_h)
    if tap_match_x_pct is not None:
        body["tap_match_x_pct"] = float(tap_match_x_pct)
    if tap_match_y_pct is not None:
        body["tap_match_y_pct"] = float(tap_match_y_pct)
    if start_step_index:
        body["start_step_index"] = int(start_step_index)
    if expires_at is not None and float(expires_at) > 0.0:
        body["expires_at"] = float(expires_at)
    body["created_at"] = float(created_at) if created_at is not None else time.time()
    return body


def dedup_zadd_args(
    body: dict[str, Any], payload: str, *, dedup_ignore_region: bool
) -> list[Any]:
    """ARGV for :data:`DEDUP_ZADD_LUA` derived from a built ``body``.

    ARGV order matches the script: ``payload, run_at(score), effective_task_type,
    player_id, instance_id, want_region, ignore_region_flag``.
    """
    region = body.get("region")
    want_region = (
        str(region).strip() if region is not None and str(region).strip() else ""
    )
    return [
        payload,
        body["run_at"],
        effective_task_type(body.get("task_type"), body.get("dsl_scenario")),
        str(body.get("player_id") or ""),
        str(body.get("instance_id") or ""),
        want_region,
        "1" if dedup_ignore_region else "0",
    ]


def enqueue_sync(
    client: redis.Redis,
    *,
    task_id: str,
    player_id: str,
    task_type: str,
    priority: int,
    run_at: float,
    instance_id: str,
    region: str | None = None,
    tap_x_pct: float | None = None,
    tap_y_pct: float | None = None,
    threshold: float | None = None,
    score: float | None = None,
    set_node: str | None = None,
    dsl_scenario: str | None = None,
    args: dict[str, Any] | None = None,
    match_top_left_x: int | None = None,
    match_top_left_y: int | None = None,
    template_w: int | None = None,
    template_h: int | None = None,
    tap_match_x_pct: float | None = None,
    tap_match_y_pct: float | None = None,
    start_step_index: int = 0,
    expires_at: float | None = None,
    created_at: float | None = None,
    skip_if_duplicate: bool = False,
    dedup_ignore_region: bool = False,
) -> bool:
    """Sync mirror of :meth:`RedisQueue.schedule` for callers holding a sync client.

    Build the canonical payload, run the atomic Lua dedup (when
    ``skip_if_duplicate``) or a plain ZADD, then publish the dashboard
    ``queue/enqueue`` event — the same three steps, in the same order, as the
    async path. Returns ``False`` when a duplicate was found and the write
    skipped; ``True`` otherwise.
    """
    body = build_queue_body(
        task_id=task_id,
        player_id=player_id,
        task_type=task_type,
        priority=priority,
        run_at=run_at,
        instance_id=instance_id,
        region=region,
        tap_x_pct=tap_x_pct,
        tap_y_pct=tap_y_pct,
        threshold=threshold,
        score=score,
        set_node=set_node,
        dsl_scenario=dsl_scenario,
        args=args,
        match_top_left_x=match_top_left_x,
        match_top_left_y=match_top_left_y,
        template_w=template_w,
        template_h=template_h,
        tap_match_x_pct=tap_match_x_pct,
        tap_match_y_pct=tap_match_y_pct,
        start_step_index=start_step_index,
        expires_at=expires_at,
        created_at=created_at,
    )
    payload = json.dumps(body)
    qk = queue_key(instance_id)

    if skip_if_duplicate:
        args_list = dedup_zadd_args(body, payload, dedup_ignore_region=dedup_ignore_region)
        rv = client.eval(DEDUP_ZADD_LUA, 1, qk, *args_list)
        try:
            applied = int(rv) == 1
        except (TypeError, ValueError):
            applied = False
        if not applied:
            logger.debug(
                "Skip duplicate queue item: player=%s type=%s region=%r",
                player_id,
                task_type,
                region,
            )
            return False
    else:
        client.zadd(qk, {payload: body["run_at"]})

    from dashboard.dashboard_events import publish_dashboard_event

    publish_dashboard_event(
        client,
        topic="queue",
        instance_id=instance_id or None,
        reason="enqueue",
    )
    return True
