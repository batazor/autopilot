"""Redis-backed adapter: bridge the pure campaign engine to live state + the bus.

Mirrors ``coordinator.dispatch`` / ``stamina.adapter``: the decision logic stays
pure (``coord.campaign.plan_campaign_tick``); this module is the thin async IO —
load the campaign config (mtime-cached, ``enabled:false`` default), build the
planner inputs from the fleet registry + per-account state, persist/load runs,
and dispatch the decision over the coord directive bus.

The scheduler's ``_run_fleet_coordinator`` calls these per active run each tick.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coord.campaign import (
    CampaignRun,
    ParticipantStatus,
    ResourceClaim,
    plan_campaign_tick,
)
from coord.campaign.model import PENDING, RUNNING, Participant
from coord.fleet import Fleet
from coord.lease import Lease
from coord.models import FleetView

from . import barriers, objective
from . import participants as parts
from .catalog import build_campaign_defs
from .directives import to_coord_directive

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from redis.asyncio import Redis

    from config.loader import Settings
    from coord.bus import DirectiveBus
    from coord.campaign import ArbitrationResult, CampaignDecision, CampaignDef
    from scheduler.queue import RedisQueue

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FLEET_ENABLED_ENV = "WOS_FLEET_ENABLED"
_FLEET_CONFIG_PATH = Path(__file__).resolve().parent / "fleet.yaml"
_FLEET_CONFIG_CACHE: dict[str, tuple[float, FleetConfig]] = {}

_RUN_TTL_S = 24 * 3600  # a finished run key self-expires so a new window re-creates


# --- config (mirror coordinator/dispatch.load_march_config) -------------------
@dataclass(frozen=True, slots=True)
class FleetConfig:
    enabled: bool = False
    campaigns: Mapping[str, bool] = field(default_factory=dict)


def _parse_fleet_config(path: Path) -> FleetConfig:
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("fleet config read failed at %s", path, exc_info=True)
        return FleetConfig()
    if not isinstance(raw, dict):
        return FleetConfig()
    campaigns_raw = raw.get("campaigns") or {}
    campaigns = {
        str(k): bool(v) for k, v in campaigns_raw.items()
    } if isinstance(campaigns_raw, dict) else {}
    return FleetConfig(enabled=bool(raw.get("enabled", False)), campaigns=campaigns)


def load_fleet_config(path: str | Path | None = None) -> FleetConfig:
    """``fleet.yaml`` parsed + cached by mtime; ``WOS_FLEET_ENABLED`` overrides the
    master switch each call (kill-switch without a file touch)."""
    p = Path(path) if path else _FLEET_CONFIG_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _FLEET_CONFIG_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        cfg = hit[1]
    else:
        cfg = _parse_fleet_config(p)
        _FLEET_CONFIG_CACHE[key] = (mtime, cfg)
    override = os.environ.get(_FLEET_ENABLED_ENV)
    if override is not None and override.strip():
        return replace(cfg, enabled=override.strip().lower() in _TRUTHY)
    return cfg


def load_campaigns(path: str | Path | None = None) -> dict[str, CampaignDef]:
    """All campaign defs with ``enabled`` overlaid (master AND per-campaign)."""
    cfg = load_fleet_config(path)
    out: dict[str, CampaignDef] = {}
    for cid, cdef in build_campaign_defs().items():
        per = bool(cfg.campaigns.get(cid, False))
        out[cid] = replace(cdef, enabled=cfg.enabled and per)
    return out


# --- planner input adapters ---------------------------------------------------
class _PlannerFleet:
    """Implements ``coord.campaign.protocols.FleetSnapshot`` over live state."""

    def __init__(self, online_fids: set[str], signals: Mapping[str, Mapping[str, Any]]) -> None:
        self._online = set(online_fids)
        self._signals = dict(signals)

    def online(self, fid: str) -> bool:
        return fid in self._online

    def signal(self, fid: str, name: str) -> bool:
        return barriers.signal_value(self._signals.get(fid, {}), name)


class _CalendarView:
    """Implements ``coord.campaign.protocols.CalendarView`` over EventWindows."""

    def __init__(self, windows: Sequence[Any]) -> None:
        self._w = {w.slug: w for w in windows}

    def window_active(self, slug: str) -> bool:
        w = self._w.get(slug)
        return bool(w and w.active)

    def ends_in_s(self, slug: str) -> float:
        w = self._w.get(slug)
        return float(w.ends_in_s) if (w and w.active) else float("inf")


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


async def _read_state(redis: Redis, fid: str) -> dict[str, str]:
    try:
        raw = await redis.hgetall(f"wos:player:{fid}:state")
    except Exception:
        logger.debug("fleet: state read failed fid=%s", fid, exc_info=True)
        return {}
    return {_decode(k): _decode(v) for k, v in (raw or {}).items()}


def _flag(state: Mapping[str, str], key: str) -> bool:
    return str(state.get(key, "")).strip().lower() in _TRUTHY


async def build_inputs(
    redis: Redis, settings: Settings, now: float
) -> tuple[list[parts.Candidate], _PlannerFleet, _CalendarView]:
    """One fleet snapshot + one state read per active account → planner inputs.

    Candidates are the *active, identified* accounts (the only ones that can act
    without a switch; multi-account-per-device switching is deferred with the
    switch scenario). Signals + online flags reuse the same reads.
    """
    fleet = Fleet(redis)
    ids = [i.instance_id for i in settings.instances]
    view = await fleet.snapshot(ids, now=now)
    candidates: list[parts.Candidate] = []
    online: set[str] = set()
    signals: dict[str, dict[str, str]] = {}
    for snap in view.instances:
        fid = snap.active_player
        if not fid:
            continue
        state = await _read_state(redis, fid)
        signals[fid] = state
        if snap.online:
            online.add(fid)
        candidates.append(
            parts.Candidate(
                fid=fid,
                instance_id=snap.instance_id,
                online=snap.online,
                alliance=snap.alliance_tag,
                role=str(state.get("planner.role", "balanced")) or "balanced",
                raid_role=str(state.get("planner.raid_role", parts.RAID_OFF)) or parts.RAID_OFF,
                events_opt_in=_flag(state, "planner.events_participate"),
                reinforce_opt_in=_flag(state, "planner.reinforce_enable"),
            )
        )
    calendar = await build_calendar_view(redis, now)
    return candidates, _PlannerFleet(online, signals), calendar


async def build_calendar_view(redis: Redis, now: float) -> _CalendarView:
    """CalendarView of active event windows.

    DEFERRED wiring: the schedule-digest source isn't plumbed to the fleet layer
    yet, so this returns an empty view (no windows active) for now — joint_event
    ships disabled, so that's correct. When the digest is wired (task: calendar
    producer), build windows via
    ``games.wos.core.calendar.coordinator_windows.event_windows_from_digest``.
    """
    return _CalendarView(())


# --- run persistence ----------------------------------------------------------
def _run_key(run_id: str) -> str:
    return f"wos:coord:campaign:run:{run_id}"


def _active_key(campaign_id: str) -> str:
    return f"wos:coord:campaign:active:{campaign_id}"


def run_to_json(run: CampaignRun) -> str:
    return json.dumps(
        {
            "campaign_id": run.campaign_id,
            "run_id": run.run_id,
            "phase_index": run.phase_index,
            "status": run.status,
            "participants": [
                {"fid": p.fid, "role": p.role, "instance_id": p.instance_id,
                 "shares_device": p.shares_device}
                for p in run.participants
            ],
            "statuses": [
                {"fid": s.fid, "reached": s.reached, "last_directive_id": s.last_directive_id,
                 "failed": s.failed, "detail": s.detail}
                for s in run.statuses
            ],
            "started_at": run.started_at,
            "phase_started_at": run.phase_started_at,
            "deadline_at": run.deadline_at,
        },
        separators=(",", ":"),
    )


def run_from_json(text: str | bytes) -> CampaignRun:
    d = json.loads(text.decode() if isinstance(text, bytes) else text)
    return CampaignRun(
        campaign_id=str(d["campaign_id"]),
        run_id=str(d["run_id"]),
        phase_index=int(d["phase_index"]),
        status=str(d["status"]),
        participants=tuple(
            Participant(
                fid=str(p["fid"]), role=str(p["role"]), instance_id=str(p["instance_id"]),
                shares_device=bool(p.get("shares_device", False)),
            )
            for p in d.get("participants", [])
        ),
        statuses=tuple(
            ParticipantStatus(
                fid=str(s["fid"]), reached=bool(s.get("reached", False)),
                last_directive_id=str(s.get("last_directive_id", "")),
                failed=bool(s.get("failed", False)), detail=str(s.get("detail", "")),
            )
            for s in d.get("statuses", [])
        ),
        started_at=float(d.get("started_at", 0.0)),
        phase_started_at=float(d.get("phase_started_at", 0.0)),
        deadline_at=float(d.get("deadline_at", 0.0)),
    )


async def save_run(redis: Redis, run: CampaignRun) -> None:
    await redis.set(_run_key(run.run_id), run_to_json(run), ex=_RUN_TTL_S)
    if run.status in (RUNNING, PENDING):
        await redis.sadd(_active_key(run.campaign_id), run.run_id)
    else:
        await redis.srem(_active_key(run.campaign_id), run.run_id)


async def load_run(redis: Redis, run_id: str) -> CampaignRun | None:
    raw = await redis.get(_run_key(run_id))
    if not raw:
        return None
    try:
        return run_from_json(raw)
    except Exception:
        logger.warning("fleet: corrupt run payload %s", run_id, exc_info=True)
        return None


async def list_active_runs(redis: Redis, campaign_id: str) -> list[CampaignRun]:
    try:
        ids = await redis.smembers(_active_key(campaign_id))
    except Exception:
        return []
    out: list[CampaignRun] = []
    for rid in ids or set():
        run = await load_run(redis, _decode(rid))
        if run is not None:
            out.append(run)
    return out


def _new_run(cdef: CampaignDef, members: Sequence[Participant], run_id: str, now: float) -> CampaignRun:
    return CampaignRun(
        campaign_id=cdef.id,
        run_id=run_id,
        phase_index=0,
        status=RUNNING,
        participants=tuple(members),
        statuses=tuple(ParticipantStatus(fid=p.fid) for p in members),
        started_at=now,
        phase_started_at=now,
        deadline_at=now + cdef.default_ttl_s,
    )


async def ensure_calendar_run(
    redis: Redis,
    cdef: CampaignDef,
    candidates: Sequence[parts.Candidate],
    calendar: _CalendarView,
    now: float,
) -> CampaignRun | None:
    """Create/refresh the run for an active calendar window (idempotent per window)."""
    slug = cdef.anchor_event_slug
    if not slug or not calendar.window_active(slug):
        return None
    run_id = f"{cdef.id}:{slug}"
    existing = await load_run(redis, run_id)
    if existing is not None:
        return existing if existing.status in (RUNNING, PENDING) else None
    members = parts.select_participants(cdef.id, candidates, max_n=cdef.max_participants)
    if len(members) < cdef.min_participants:
        return None
    run = _new_run(cdef, members, run_id, now)
    await save_run(redis, run)
    logger.info("fleet: created calendar run %s with %d participants", run_id, len(members))
    return run


async def create_run(
    redis: Redis,
    cdef: CampaignDef,
    members: Sequence[Participant],
    *,
    run_id: str,
    now: float,
) -> CampaignRun:
    """Create a run for a notify/manual trigger (the caller supplies a stable id)."""
    run = _new_run(cdef, members, run_id, now)
    await save_run(redis, run)
    return run


async def active_runs_for(
    redis: Redis,
    cdef: CampaignDef,
    candidates: Sequence[parts.Candidate],
    calendar: _CalendarView,
    now: float,
) -> list[CampaignRun]:
    """The runs to drive this tick for a campaign (calendar ensures, others load)."""
    if cdef.trigger == "calendar":
        run = await ensure_calendar_run(redis, cdef, candidates, calendar, now)
        return [run] if run is not None else []
    return await list_active_runs(redis, cdef.id)


# --- arbitration (fleet-level resource contention) ----------------------------
_FLEET_BOTTLENECK_KEY = "wos:coord:fleet:bottleneck"


def build_claim(
    cdef: CampaignDef, run: CampaignRun, now: float, *, value_factor: float = 1.0
) -> ResourceClaim:
    """A run's resource bid: each participant reserves its account + device, at
    the campaign's cross-campaign priority. Reserving for the whole run (not just
    ticks that emit directives) keeps an event from stealing a raid's fighter
    mid-raid. ``value_factor`` (raid ROI) lets a fat raid outrank a marginal one;
    defaults 1.0 until the troop/resource readers feed real ROI."""
    resources: set[str] = set()
    for p in run.participants:
        resources.add(f"account:{p.fid}")
        if p.instance_id:
            resources.add(f"device:{p.instance_id}")
    return ResourceClaim(
        run_id=run.run_id,
        priority=objective.campaign_priority(cdef, run, now, value_factor=value_factor),
        resources=frozenset(resources),
        detail=cdef.id,
    )


def partition_by_safety(
    pairs: Sequence[tuple[CampaignDef, CampaignRun]], calendar: _CalendarView
) -> tuple[list[tuple[CampaignDef, CampaignRun]], list[tuple[str, str]]]:
    """Split runs into dispatchable vs safety-suppressed (war/hunt keep-home +
    don't-raid-an-event-participant), using the active calendar windows."""
    from . import safety

    event_fids = frozenset(
        p.fid for cdef, run in pairs if cdef.id == "joint_event" for p in run.participants
    )
    ctx = safety.SafetyContext(
        event_fids=event_fids,
        war_active=calendar.window_active(safety.WAR_SLUG),
        hunt_active=calendar.window_active(safety.HUNT_SLUG),
    )
    safe: list[tuple[CampaignDef, CampaignRun]] = []
    suppressed: list[tuple[str, str]] = []
    for cdef, run in pairs:
        verdict = safety.check_dispatch(
            cdef.id, [p.fid for p in run.participants], ctx
        )
        if verdict.allowed:
            safe.append((cdef, run))
        else:
            suppressed.append((run.run_id, verdict.reason))
    return safe, suppressed


async def write_fleet_bottleneck(
    redis: Redis,
    result: ArbitrationResult,
    now: float,
    *,
    suppressed: Sequence[tuple[str, str]] = (),
) -> None:
    """Publish the contention snapshot (active/starved/contended/suppressed)."""
    payload = json.dumps(
        {
            "at": now,
            "active": list(result.active),
            "starved": list(result.starved),
            "contended": list(result.contended),
            "suppressed": [{"run": r, "reason": why} for r, why in suppressed],
        },
        separators=(",", ":"),
    )
    try:
        await redis.set(_FLEET_BOTTLENECK_KEY, payload, ex=300)
    except Exception:
        logger.debug("fleet bottleneck write failed", exc_info=True)


# --- dispatch (mirror dispatch_march) -----------------------------------------
def campaign_lock(redis: Redis, run_id: str) -> Lease:
    """Per-run orchestrator lock — only one driver advances a given run."""
    return Lease(f"campaign:{run_id}", redis=redis)


@dataclass(frozen=True, slots=True)
class FleetDispatch:
    posted: int = 0
    deferred: int = 0
    no_scenario: int = 0
    advanced_to: int | None = None
    status: str = RUNNING


async def dispatch_decision(
    redis: Redis,
    bus: DirectiveBus,
    cdef: CampaignDef,
    run: CampaignRun,
    decision: CampaignDecision,
    now: float,
) -> FleetDispatch:
    """Post the decision's wired directives over the bus, persist the new run
    state, and de-index finished runs. Held under the per-run lease, so two
    overlapping ticks can't double-advance."""
    posted = deferred = no_scenario = 0
    empty_view = FleetView(())
    for sd in decision.directives:
        directive, status = to_coord_directive(sd)
        if directive is None:
            if status == "deferred":
                deferred += 1
            else:
                no_scenario += 1
            continue
        directive = replace(directive, created_at=now, ttl_s=cdef.default_ttl_s)
        await bus.post(directive, empty_view, now=now)
        posted += 1

    if decision.advance_to is not None:
        new_run = replace(
            run,
            phase_index=decision.advance_to,
            phase_started_at=now,
            statuses=decision.updated_statuses,
            status=decision.next_status,
        )
    else:
        new_run = replace(run, statuses=decision.updated_statuses, status=decision.next_status)
    await save_run(redis, new_run)

    if posted or deferred or decision.advance_to is not None or new_run.status != run.status:
        logger.info(
            "fleet dispatch run=%s phase=%d->%s posted=%d deferred=%d status=%s trace=%s",
            run.run_id, run.phase_index, decision.advance_to, posted, deferred,
            new_run.status, ",".join(decision.trace),
        )
    return FleetDispatch(
        posted=posted, deferred=deferred, no_scenario=no_scenario,
        advanced_to=decision.advance_to, status=new_run.status,
    )


async def run_campaign_tick(
    redis: Redis,
    queue: RedisQueue,
    bus: DirectiveBus,
    cdef: CampaignDef,
    run: CampaignRun,
    fleet: _PlannerFleet,
    calendar: _CalendarView,
    now: float,
) -> FleetDispatch:
    """Plan (pure) + dispatch (IO) one run this tick.

    Computes the optimal shared-device service order (device scheduler) and feeds
    it to the planner so the highest-value account on a contended device is
    serviced first within the event window."""
    from . import device_jobs

    device_order = device_jobs.optimized_device_order(run, now=now)
    decision = plan_campaign_tick(cdef, run, fleet, calendar, now, device_order=device_order)
    return await dispatch_decision(redis, bus, cdef, run, decision, now)
