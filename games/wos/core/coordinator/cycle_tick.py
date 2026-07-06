"""Autonomous development-cycle tick — the arbiter actually at the wheel.

:func:`cycle.plan_cycle` (the unified development arbitration with calendar /
daily / economy / safety / feedback biases) was only ever invoked from the
dashboard's what-if calculator; the autonomous bot ran building and research on
independent crons with no cross-domain view. This module runs the SAME
arbitration inside the scheduler, per player, on an interval:

* **v1 domains**: building + research — the two whose per-account facts
  (``buildings.levels.*`` / ``research.levels.*``) have live readers with
  state-hash parsers. Other domains join by extending
  :func:`collect_cycle_inputs` as their fact coverage lands.
* **Trace always**: every tick appends a JSON decision snapshot (winning
  commits, top candidates, biases, feedback) to a per-player ring buffer —
  ``botctl why``-grade explainability for the development channels, mirroring
  the march trace.
* **Dispatch behind a flag**: ``cycle.yaml dispatch: false`` ships trace-only
  so live traces can be validated before the tick starts steering the queue
  (the standalone building/research crons keep dispatching meanwhile). Flip
  ``dispatch: true`` to let the arbiter enqueue its top commits — the mapping
  lives in :data:`CYCLE_DISPATCH_SCENARIOS`, dedup + push-ttl keep it from
  double-driving the domain crons.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cycle import CyclePlan, plan_cycle
from .domains import dev_channels
from .model import CONSTRUCTION, Channel

if TYPE_CHECKING:
    from collections.abc import Mapping

    from redis.asyncio import Redis

    from scheduler.queue import RedisQueue

    from .feedback import FeedbackState

logger = logging.getLogger(__name__)

_CYCLE_CONFIG_PATH = Path(__file__).resolve().parent / "cycle.yaml"
_CYCLE_CONFIG_CACHE: dict[str, tuple[float, CycleConfig]] = {}
_CYCLE_ENABLED_ENV = "WOS_CYCLE_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}

TRACE_RETENTION_SECONDS = 24 * 3600
TRACE_RETENTION_CAP = 50
# In-memory per-player signature of the last traced outcome (the scheduler is
# one long-lived process) — keeps the ring buffer to real changes.
_CYCLE_TRACE_SIG: dict[str, str] = {}
# Per-player monotonic-ish stamp of the last tick (wall clock is fine here).
_CYCLE_LAST_RUN: dict[str, float] = {}

# Domain → the scenario its commit dispatches to when ``dispatch: true``.
# Only domains with a safe, idempotent runnable belong here; anything absent is
# trace-only (reported as ``no_scenario`` in the trace, never silently dropped).
CYCLE_DISPATCH_SCENARIOS: dict[str, str] = {
    "building_progression": "building.plan_tick.cron",
    "building_economy": "building.plan_tick.cron",
    "research": "research.plan_tick.cron",
}
DISPATCH_PRIORITY_BASE = 60_000


@dataclass(frozen=True, slots=True)
class CycleConfig:
    enabled: bool = True
    interval_s: float = 600.0
    dispatch: bool = False


def _parse_cycle_config(path: Path) -> CycleConfig:
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("cycle config read failed at %s", path, exc_info=True)
        return CycleConfig()
    if not isinstance(raw, dict):
        return CycleConfig()
    return CycleConfig(
        enabled=bool(raw.get("enabled", True)),
        interval_s=float(raw.get("interval_s", 600.0)),
        dispatch=bool(raw.get("dispatch", False)),
    )


def load_cycle_config(path: str | Path | None = None) -> CycleConfig:
    """``cycle.yaml`` parsed + mtime-cached; env kill-switch re-checked per call."""
    p = Path(path) if path else _CYCLE_CONFIG_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _CYCLE_CONFIG_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        cfg = hit[1]
    else:
        cfg = _parse_cycle_config(p)
        _CYCLE_CONFIG_CACHE[key] = (mtime, cfg)
    override = os.environ.get(_CYCLE_ENABLED_ENV)
    if override is not None and override.strip():
        return replace(cfg, enabled=override.strip().lower() in _TRUTHY)
    return cfg


def _read_prefixed_ints(state: Mapping[str, Any], prefix: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (state or {}).items():
        ks = str(k)
        if not ks.startswith(prefix) or ks.endswith((".synced_at", "_at", "_text", "_confidence")):
            continue
        try:
            out[ks[len(prefix):]] = int(float(str(v)))
        except (TypeError, ValueError):
            continue
    return out


def collect_cycle_inputs(state: Mapping[str, Any]) -> dict[str, Any]:
    """Build ``plan_cycle`` kwargs from a decoded player-state hash.

    Per-domain and defensive: a domain whose facts are absent (reader hasn't
    run) contributes ``None`` and the arbitration degrades gracefully — same
    contract as ``plan_cycle`` itself. Returns {} when NO domain has data, so
    the caller can skip the tick (blind player) instead of tracing noise.
    """
    kwargs: dict[str, Any] = {}

    build_levels = _read_prefixed_ints(state, "buildings.levels.")
    if build_levels:
        try:
            from games.wos.core.building.planner import load_graph, plan_builds

            bg = load_graph()
            kwargs["build_slate"] = plan_builds(bg, build_levels)
            kwargs["build_graph"] = bg
        except Exception:
            logger.debug("cycle tick: building inputs failed", exc_info=True)

    research_levels = _read_prefixed_ints(state, "research.levels.")
    if research_levels:
        try:
            from games.wos.core.research.planner import plan_next as research_plan_next
            from games.wos.core.research.planner.model import load_research_graph

            rc_level = 0
            try:
                rc_level = int(float(str(state.get("research.center.level") or 0)))
            except (TypeError, ValueError):
                rc_level = 0
            rg = load_research_graph()
            kwargs["research_plan"] = research_plan_next(rg, research_levels, rc_level)
            kwargs["research_graph"] = rg
        except Exception:
            logger.debug("cycle tick: research inputs failed", exc_info=True)

    return kwargs


def cycle_trace_key(player_id: str) -> str:
    return f"wos:player:{player_id}:cycle_decisions"


def cycle_decision_payload(plan: CyclePlan, *, now: float, dispatched: list[str]) -> dict[str, Any]:
    """JSON-able snapshot of one cycle tick (shape parallels the march trace)."""
    commits = [
        {
            "channel": c.channel_id,
            "domain": c.action.domain,
            "key": c.action.key,
            "priority": round(float(c.action.priority), 2),
            "detail": c.action.detail,
        }
        for c in plan.decision.commits
    ]
    return {
        "ts": now,
        "action": "dispatch" if dispatched else ("plan" if commits else "idle"),
        "commits": commits,
        "dispatched": dispatched,
        "starved": [
            {"domain": s.domain, "cost": dict(s.cost)} for s in plan.decision.starved
        ],
        "boosts": {k: round(float(v), 3) for k, v in plan.boosts.items()},
        "feedback": {"stuck": list(plan.feedback.stuck), "held": list(plan.feedback.held)},
        "safe_mode": bool(plan.safety.safe_mode),
        "candidates": len(plan.candidates),
    }


def _cycle_signature(payload: dict[str, Any]) -> str:
    commits = ",".join(sorted(f"{c['domain']}:{c['key']}" for c in payload["commits"]))
    return f"{payload['action']}|{commits}|{','.join(payload['dispatched'])}"


async def run_cycle_tick(
    *,
    redis: Redis,
    queue: RedisQueue,
    instance_id: str,
    player_id: str,
    state: Mapping[str, Any],
    now: float,
    feedback_state: FeedbackState | None = None,
    config: CycleConfig | None = None,
) -> CyclePlan | None:
    """One arbitration pass for one player: inputs → plan_cycle → trace (→ queue).

    Returns the plan (None when the player is blind / the tick is throttled).
    The caller (scheduler) provides the decoded state hash it already read.
    """
    cfg = config or load_cycle_config()
    if not cfg.enabled:
        return None
    last = _CYCLE_LAST_RUN.get(player_id, 0.0)
    if now - last < cfg.interval_s:
        return None
    _CYCLE_LAST_RUN[player_id] = now

    inputs = collect_cycle_inputs(state)
    if not inputs:
        return None  # fully blind player — nothing to arbitrate, don't trace noise

    channels = [Channel(id="construction_1", kind=CONSTRUCTION)]
    channels += [Channel(id=cid, kind=kind) for cid, kind in dev_channels()]

    plan = plan_cycle(
        channels=channels,
        balances={},
        feedback_state=feedback_state,
        **inputs,
    )

    dispatched: list[str] = []
    if cfg.dispatch:
        for commit in plan.decision.commits:
            scenario = CYCLE_DISPATCH_SCENARIOS.get(commit.action.domain)
            if scenario is None:
                continue
            ok = await queue.schedule(
                task_id=f"cycle:{commit.action.domain}:{int(now)}",
                player_id=player_id,
                task_type=scenario,
                priority=DISPATCH_PRIORITY_BASE + int(commit.action.priority),
                run_at=now,
                instance_id=instance_id,
                dsl_scenario=scenario,
                skip_if_duplicate=True,
                dedup_ignore_region=True,
            )
            if ok:
                dispatched.append(scenario)

    payload = cycle_decision_payload(plan, now=now, dispatched=dispatched)
    sig = _cycle_signature(payload)
    if _CYCLE_TRACE_SIG.get(player_id) != sig:
        try:
            key = cycle_trace_key(player_id)
            member = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            pipe = redis.pipeline(transaction=False)
            pipe.zadd(key, {member: now})
            pipe.zremrangebyscore(key, 0, now - TRACE_RETENTION_SECONDS)
            pipe.zremrangebyrank(key, 0, -(TRACE_RETENTION_CAP + 1))
            pipe.expire(key, TRACE_RETENTION_SECONDS)
            await pipe.execute()
            _CYCLE_TRACE_SIG[player_id] = sig
        except Exception:
            logger.debug("cycle decision trace write failed", exc_info=True)
    return plan
