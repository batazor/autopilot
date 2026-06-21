"""Read-only observability for the cross-instance coordination layer.

Surfaces the fleet registry, the ``fid → instance`` reverse index, in-flight
directives, the audit stream, and barrier states — the single pane for debugging
"who told whom to do what, and did it happen". Mutating endpoints (post a
directive, open a barrier) intentionally live with the orchestrator, not here.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, Query

from api.deps import get_redis
from api.services.instances import list_instance_ids
from coord import keys
from coord.models import InstanceSnapshot

router = APIRouter(prefix="/api/coord", tags=["coord"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def _s(value: Any) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _k(value: Any) -> str:
    return _s(value)


@router.get("/fleet")
def coord_fleet(client: RedisDep) -> dict[str, Any]:
    """Live view of every instance (active player, alliance, online, march slots)."""
    now = time.time()
    fleet = [
        InstanceSnapshot.from_hash(
            iid, client.hgetall(keys.instance_state_key(iid)) or {}, now=now
        ).to_dict()
        for iid in list_instance_ids()
    ]
    return {"fleet": fleet}


@router.get("/fid/{fid}")
def coord_fid(fid: str, client: RedisDep) -> dict[str, Any]:
    """Which device a fid is ACTIVE on right now, and whether the claim is valid."""
    now = time.time()
    val = _s(client.hget(keys.FID_ACTIVE_KEY, fid))
    if not val:
        return {"fid": fid, "instance_id": None, "online": False}
    iid = val.split("|", 1)[0]
    got = client.hmget(
        keys.instance_state_key(iid), [keys.FIELD_ACTIVE_PLAYER, keys.FIELD_COORD_SEEN_AT]
    )
    active = _s(got[0] if got else None)
    seen = _s(got[1] if got and len(got) > 1 else None)
    try:
        seen_f = float(seen) if seen else 0.0
    except (TypeError, ValueError):
        seen_f = 0.0
    online = seen_f > 0.0 and (now - seen_f) <= keys.FLEET_STALE_AFTER_S and active == fid
    return {"fid": fid, "instance_id": iid, "online": online}


@router.get("/audit")
def coord_audit(
    client: RedisDep, count: Annotated[int, Query(ge=1, le=2000)] = 200
) -> dict[str, Any]:
    """Most-recent-first directive/barrier/lease events (the audit stream)."""
    try:
        rows = client.xrevrange(keys.AUDIT_STREAM, count=count)
    except Exception:
        rows = []
    out = [
        {**{_k(k): _s(v) for k, v in fields.items()}, "id": _s(entry_id)}
        for entry_id, fields in rows
    ]
    return {"audit": out}


@router.get("/directives/inflight")
def coord_directives_inflight(client: RedisDep) -> dict[str, Any]:
    """Directive status hashes not yet in a terminal state."""
    out: list[dict[str, str]] = []
    for key in client.scan_iter(match=f"{keys.PREFIX}:directive:status:*"):
        row = {_k(k): _s(v) for k, v in (client.hgetall(key) or {}).items()}
        if row.get("state") in ("pending", "running"):
            out.append(row)
    return {"inflight": out}


@router.get("/contention")
def coord_contention(client: RedisDep) -> dict[str, Any]:
    """Latest fleet-arbitration snapshot: which runs got resources, which were
    starved, and the contended resources (the fleet bottleneck)."""
    raw = _s(client.get("wos:coord:fleet:bottleneck"))  # adapter._FLEET_BOTTLENECK_KEY
    if not raw:
        return {"contention": None}
    try:
        return {"contention": json.loads(raw)}
    except (ValueError, TypeError):
        return {"contention": None}


@router.get("/barriers")
def coord_barriers(client: RedisDep) -> dict[str, Any]:
    """Barrier hashes (spec + status); excludes the arrived/events sub-keys."""
    out: list[dict[str, str]] = []
    for key in client.scan_iter(match=f"{keys.PREFIX}:barrier:*"):
        ks = _k(key)
        if ks.endswith((":arrived", ":events")):
            continue
        row = {_k(k): _s(v) for k, v in (client.hgetall(key) or {}).items()}
        if row:
            row["key"] = ks
            out.append(row)
    return {"barriers": out}
