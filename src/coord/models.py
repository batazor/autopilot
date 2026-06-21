"""Pure data model for the coordination layer — no Redis, no IO.

Frozen dataclasses mirroring ``coordinator.model`` style. Everything here is
JSON-serialisable and constructed from plain dicts (a decoded Redis hash, a
parsed inbox payload) so the routing / barrier / fleet logic can be unit-tested
with no Redis at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import keys

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping


# --- small coercion helpers (Redis hands back strings / bytes) ----------------
def _s(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _to_float(value: Any) -> float:
    try:
        return float(_s(value)) if _s(value) != "" else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int_or_none(value: Any) -> int | None:
    s = _s(value)
    if s == "":
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


# --- Fleet ---------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class InstanceSnapshot:
    """One instance's coord-relevant state, derived from its state hash."""

    instance_id: str
    active_player: str = ""
    alliance_tag: str = ""
    game: str = ""
    current_screen: str = ""
    state: str = ""
    paused: bool = False
    march_slots_total: int | None = None
    march_slots_free: int | None = None
    coord_seen_at: float = 0.0
    online: bool = False

    @classmethod
    def from_hash(
        cls,
        instance_id: str,
        hash_: Mapping[str, Any],
        *,
        now: float,
        stale_after: float = keys.FLEET_STALE_AFTER_S,
    ) -> InstanceSnapshot:
        seen = _to_float(hash_.get(keys.FIELD_COORD_SEEN_AT))
        online = seen > 0.0 and (now - seen) <= stale_after
        return cls(
            instance_id=str(instance_id),
            active_player=_s(hash_.get(keys.FIELD_ACTIVE_PLAYER)),
            alliance_tag=_s(hash_.get(keys.FIELD_ALLIANCE_TAG)),
            game=_s(hash_.get(keys.FIELD_GAME)),
            current_screen=_s(hash_.get(keys.FIELD_CURRENT_SCREEN)),
            state=_s(hash_.get(keys.FIELD_STATE)),
            paused=_s(hash_.get(keys.FIELD_PAUSED)) == "1",
            march_slots_total=_to_int_or_none(hash_.get(keys.FIELD_MARCH_SLOTS_TOTAL)),
            march_slots_free=_to_int_or_none(hash_.get(keys.FIELD_MARCH_SLOTS_FREE)),
            coord_seen_at=seen,
            online=online,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "active_player": self.active_player,
            "alliance_tag": self.alliance_tag,
            "game": self.game,
            "current_screen": self.current_screen,
            "state": self.state,
            "paused": self.paused,
            "march_slots_total": self.march_slots_total,
            "march_slots_free": self.march_slots_free,
            "coord_seen_at": self.coord_seen_at,
            "online": self.online,
        }


@dataclass(frozen=True, slots=True)
class FleetView:
    """A snapshot of the whole fleet — the pure input to directive routing."""

    instances: tuple[InstanceSnapshot, ...] = ()

    def get(self, instance_id: str) -> InstanceSnapshot | None:
        for inst in self.instances:
            if inst.instance_id == instance_id:
                return inst
        return None

    def online_instances(self) -> tuple[InstanceSnapshot, ...]:
        return tuple(i for i in self.instances if i.online)

    def instance_for_fid(self, fid: str) -> str | None:
        """The ONLINE instance a fid is currently active on, or None.

        Advisory: cross-checks that the instance still reports this fid as its
        ``active_player`` (the source of truth) and is fresh.
        """
        fid = str(fid or "")
        if not fid:
            return None
        for inst in self.instances:
            if inst.online and inst.active_player == fid:
                return inst.instance_id
        return None

    def instances_for_alliance(self, tag: str) -> list[str]:
        tag = str(tag or "")
        return [i.instance_id for i in self.instances if i.online and i.alliance_tag == tag]


# --- Directive bus -------------------------------------------------------------
TARGET_INSTANCE = "instance"
TARGET_FID = "fid"
TARGET_ALL = "all"
TARGET_ALLIANCE = "alliance"


@dataclass(frozen=True, slots=True)
class DirectiveTarget:
    """Where a directive should land. Resolved to instance ids by ``routing``."""

    kind: str            # instance | fid | all | alliance
    value: str = ""      # instance_id | fid | alliance tag ; "" for all

    @classmethod
    def instance(cls, instance_id: str) -> DirectiveTarget:
        return cls(TARGET_INSTANCE, str(instance_id))

    @classmethod
    def fid(cls, fid: str) -> DirectiveTarget:
        return cls(TARGET_FID, str(fid))

    @classmethod
    def all_(cls) -> DirectiveTarget:
        return cls(TARGET_ALL, "")

    @classmethod
    def alliance(cls, tag: str) -> DirectiveTarget:
        return cls(TARGET_ALLIANCE, str(tag))


@dataclass(frozen=True, slots=True)
class Directive:
    """A structured command one party posts for a worker to consume.

    ``kind`` selects the handler (ping / enqueue_scenario / request_account_switch
    / barrier_signal / noop). ``idempotency_key`` gates redelivery + duplicate
    posts at the bus (SADD-claim); empty falls back to ``directive_id``.
    """

    directive_id: str
    kind: str
    target: DirectiveTarget
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = ""
    created_at: float = 0.0
    ttl_s: float = 0.0
    idempotency_key: str = ""

    def dedup_key(self) -> str:
        return self.idempotency_key or self.directive_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "kind": self.kind,
            "target": {"kind": self.target.kind, "value": self.target.value},
            "payload": dict(self.payload),
            "source": self.source,
            "created_at": self.created_at,
            "ttl_s": self.ttl_s,
            "idempotency_key": self.idempotency_key,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Directive:
        tgt = data.get("target") or {}
        target = DirectiveTarget(
            kind=_s(tgt.get("kind")) or TARGET_INSTANCE,
            value=_s(tgt.get("value")),
        )
        payload = data.get("payload")
        return cls(
            directive_id=_s(data.get("directive_id")),
            kind=_s(data.get("kind")),
            target=target,
            payload=dict(payload) if isinstance(payload, dict) else {},
            source=_s(data.get("source")),
            created_at=_to_float(data.get("created_at")),
            ttl_s=_to_float(data.get("ttl_s")),
            idempotency_key=_s(data.get("idempotency_key")),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> Directive:
        text = raw.decode() if isinstance(raw, bytes) else raw
        return cls.from_dict(json.loads(text))


# directive lifecycle states (written to the status hash + audit stream)
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DirectiveStatus:
    directive_id: str
    instance_id: str = ""
    state: str = STATUS_PENDING
    started_at: float = 0.0
    finished_at: float = 0.0
    result: str = ""
    error: str = ""

    @classmethod
    def from_hash(cls, directive_id: str, hash_: Mapping[str, Any]) -> DirectiveStatus:
        return cls(
            directive_id=str(directive_id),
            instance_id=_s(hash_.get("instance_id")),
            state=_s(hash_.get("state")) or STATUS_PENDING,
            started_at=_to_float(hash_.get("started_at")),
            finished_at=_to_float(hash_.get("finished_at")),
            result=_s(hash_.get("result")),
            error=_s(hash_.get("error")),
        )


# --- Barrier / rendezvous ------------------------------------------------------
BARRIER_WAITING = "waiting"
BARRIER_READY = "ready"
BARRIER_TIMED_OUT = "timed_out"
BARRIER_ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class BarrierSpec:
    """A quorum-or-deadline rendezvous. Named conditions live one layer up
    (the campaign maps "city_empty" → a 1-party barrier and arrives on it)."""

    barrier_id: str
    required_n: int
    deadline_ts: float
    group: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "barrier_id": self.barrier_id,
                "required_n": self.required_n,
                "deadline_ts": self.deadline_ts,
                "group": self.group,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> BarrierSpec:
        text = raw.decode() if isinstance(raw, bytes) else raw
        d = json.loads(text)
        return cls(
            barrier_id=_s(d.get("barrier_id")),
            required_n=int(d.get("required_n", 1)),
            deadline_ts=_to_float(d.get("deadline_ts")),
            group=_s(d.get("group")),
        )


@dataclass(frozen=True, slots=True)
class BarrierState:
    spec: BarrierSpec
    arrived: tuple[str, ...]
    status: str
    created_at: float = 0.0

    def arrived_set(self) -> Collection[str]:
        return set(self.arrived)
