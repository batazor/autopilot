"""Pure data model + affordability math for the shared resource world.

No Redis, no ADB, no game IO — every function here is deterministic and unit
testable. The Redis-backed :mod:`adapter` resolves live ``wos:player:<pid>:state``
into the :class:`WorldView` snapshots this module reasons over.

Why a unified "world" instead of one allocator per resource: a single in-game
action spends *several* resources at once (an intel run takes a march slot AND
stamina AND troops; a beast hunt takes a slot, stamina, troops and heroes). A
per-resource allocator would green-light an action it can't actually afford on a
sibling resource. So every action declares a *cost vector* and the allocator
checks the whole bundle through :func:`can_afford`.

Resource kinds (semantics differ, the interface doesn't):

* ``pool_regen``   — capped pool that regenerates over time (stamina). Spent for
  good; a fresh OCR read overwrites the interpolated estimate upstream.
* ``slot_lease``   — concurrency cap (march queues, 2..6). Not "spent" — *held*
  for the march duration and released when its timer ends.
* ``typed_pool``   — a pool partitioned by type (troops: infantry/lancer/...),
  leased in chunks per march and returned (minus wounded) on arrival.
* ``exclusive_set``— a set of individual, role-tagged units (heroes) each held
  by at most one march at a time.

Observability: a resource only constrains decisions once a reader populates it.
Troops and heroes ship ``observed: false`` until their OCR readers exist; the
``unobserved_policy`` ("block" | "optimistic") decides whether an unread
resource blocks the action or is assumed available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_ACTIONS_PATH = _MODULE_DIR / "actions.yaml"

DEFAULT_SLOT_CAPACITY = 6

# --- Well-known resource ids -------------------------------------------------
SLOT_RESOURCE = "march_slots"
STAMINA_RESOURCE = "stamina"
TROOPS_RESOURCE = "troops"
HEROES_RESOURCE = "heroes"

# --- Per-cost blocking reasons (surfaced in the decision trace) --------------
NO_FREE_SLOT = "no_free_slot"
INSUFFICIENT_STAMINA = "insufficient_stamina"
NO_TROOPS = "no_troops"
NO_FREE_HERO = "no_free_hero"
UNOBSERVED_BLOCKED = "unobserved_blocked"

_KIND_BLOCK = {
    "slot_lease": NO_FREE_SLOT,
    "pool_regen": INSUFFICIENT_STAMINA,
    "typed_pool": NO_TROOPS,
    "exclusive_set": NO_FREE_HERO,
}


def _as_opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def round_trip_seconds(
    travel_out_s: float,
    participation_s: float = 0.0,
    *,
    return_ratio: float = 1.0,
) -> int:
    """Lease duration for a march that travels out, does something, and returns.

    A rally join (or any away march) ties up its slot/troops/heroes for the whole
    round trip: out + participation + back, where the return leg is ~1:1 with the
    outbound (``return_ratio=1.0``). Use this to set ``lease_seconds`` at dispatch
    once the travel time to the target is known (distance-dependent).
    """
    return int(travel_out_s + participation_s + travel_out_s * return_ratio)


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One resource declared in ``actions.yaml``'s ``resources:`` block."""

    id: str
    kind: str
    observed: bool = True
    cap: int | None = None          # pool_regen
    regen_per_hour: float = 0.0     # pool_regen
    types: tuple[str, ...] = ()     # typed_pool
    roles: tuple[str, ...] = ()     # exclusive_set

    @classmethod
    def from_dict(cls, rid: str, raw: dict[str, Any]) -> ResourceSpec:
        return cls(
            id=rid,
            kind=str(raw["kind"]),
            observed=bool(raw.get("observed", True)),
            cap=_as_opt_int(raw.get("cap")),
            regen_per_hour=float(raw.get("regen_per_hour", 0.0)),
            types=tuple(str(t) for t in (raw.get("types") or ())),
            roles=tuple(str(r) for r in (raw.get("roles") or ())),
        )


@dataclass(frozen=True, slots=True)
class Cost:
    """One line of an action's cost vector — what it spends on one resource."""

    resource: str
    amount: int = 0          # slot count / stamina points / troop count
    type: str | None = None  # troop type ("any" or a specific type)
    role: str | None = None  # hero role ("any" or a specific role)
    count: int = 0           # hero count

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Cost:
        return cls(
            resource=str(raw["resource"]),
            amount=int(raw.get("amount", 0)),
            type=(str(raw["type"]) if raw.get("type") else None),
            role=(str(raw["role"]) if raw.get("role") else None),
            count=int(raw.get("count", 0)),
        )


@dataclass(frozen=True, slots=True)
class Action:
    """One resource consumer (a raid / march) declared in ``actions.yaml``."""

    id: str
    task_type: str
    priority: int = 0
    active_when: str | None = None   # python-expr cond; None → always active
    daily_quota: int | None = None   # None → unlimited
    enabled: bool = True             # per-action gate (turn on observed subset)
    lease_seconds: int = 0           # how long the leased slot/troops/heroes are held
                                     # (gathering runs for HOURS; a beast hunt is brief)
    costs: tuple[Cost, ...] = ()
    reserve: Mapping[str, int] = field(default_factory=dict)  # resource → held units

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Action:
        return cls(
            id=str(raw["id"]),
            task_type=str(raw.get("task_type") or raw["id"]),
            priority=int(raw.get("priority", 0)),
            active_when=(str(raw["active_when"]) if raw.get("active_when") else None),
            daily_quota=_as_opt_int(raw.get("daily_quota")),
            enabled=bool(raw.get("enabled", True)),
            lease_seconds=int(raw.get("lease_seconds", 0)),
            costs=tuple(Cost.from_dict(c) for c in (raw.get("costs") or [])),
            reserve={str(k): int(v) for k, v in (raw.get("reserve") or {}).items()},
        )

    def slot_cost(self) -> int:
        return sum(c.amount for c in self.costs if c.resource == SLOT_RESOURCE)

    def stamina_cost(self) -> int:
        return sum(c.amount for c in self.costs if c.resource == STAMINA_RESOURCE)


@dataclass(frozen=True, slots=True)
class ActionTable:
    """Parsed ``actions.yaml`` — the declarative resource world for one game."""

    enabled: bool = False
    unobserved_policy: str = "block"   # "block" | "optimistic"
    daily_reset_utc: str = "00:00"
    resources: tuple[ResourceSpec, ...] = ()
    actions: tuple[Action, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ActionTable:
        raw = raw or {}
        resources = tuple(
            ResourceSpec.from_dict(rid, spec)
            for rid, spec in (raw.get("resources") or {}).items()
        )
        return cls(
            enabled=bool(raw.get("enabled", False)),
            unobserved_policy=str(raw.get("unobserved_policy", "block")),
            daily_reset_utc=str(raw.get("daily_reset_utc", "00:00")),
            resources=resources,
            actions=tuple(Action.from_dict(a) for a in (raw.get("actions") or [])),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> ActionTable:
        p = Path(path) if path else DEFAULT_ACTIONS_PATH
        return cls.from_dict(yaml.safe_load(p.read_text(encoding="utf-8")))

    def spec(self, resource_id: str) -> ResourceSpec | None:
        return next((s for s in self.resources if s.id == resource_id), None)

    def specs_by_id(self) -> dict[str, ResourceSpec]:
        return {s.id: s for s in self.resources}


@dataclass(frozen=True, slots=True)
class WorldView:
    """Live availability of every resource for one player, after subtracting
    in-flight occupancy and held reservations. Built by the adapter; consumed by
    :func:`can_afford` and the allocator. Pure data — no IO."""

    slots_capacity: int
    slots_free: int
    stamina_est: float | None
    troops_free: Mapping[str, int]               # type → available count
    troops_observed: bool
    free_heroes: Mapping[str, tuple[str, ...]]    # role → free hero ids
    heroes_observed: bool

    def heroes_for(self, role: str | None) -> tuple[str, ...]:
        if role in (None, "any"):
            out: list[str] = []
            for ids in self.free_heroes.values():
                out.extend(ids)
            return tuple(out)
        return tuple(self.free_heroes.get(role, ()))

    def troops_for(self, troop_type: str | None) -> int:
        if troop_type in (None, "any"):
            return sum(self.troops_free.values())
        return int(self.troops_free.get(troop_type, 0))


@dataclass(frozen=True, slots=True)
class Block:
    """One unmet cost line — which resource blocked and why."""

    resource: str
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Assignment:
    """Concrete units an action would lease if dispatched (for the reservation)."""

    heroes: tuple[str, ...] = ()
    troops: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Affordability:
    """Whether one action's whole cost vector fits the world right now."""

    ok: bool
    blocks: tuple[Block, ...] = ()
    assignment: Assignment | None = None


def can_afford(
    action: Action,
    world: WorldView,
    table: ActionTable,
    *,
    unobserved_policy: str,
) -> Affordability:
    """Check every cost line of ``action`` against ``world``.

    Collects *all* blocking resources (not just the first) so the trace can
    explain the full picture, and — when affordable — returns the concrete
    :class:`Assignment` (which heroes / how many troops) for the reservation.
    Unobserved resources (no reader yet) block under ``"block"`` policy and are
    assumed available under ``"optimistic"``.
    """
    blocks: list[Block] = []
    heroes: list[str] = []
    troops: dict[str, int] = {}

    for c in action.costs:
        spec = table.spec(c.resource)
        if spec is None:
            continue

        if not spec.observed:
            if unobserved_policy == "block":
                blocks.append(Block(c.resource, UNOBSERVED_BLOCKED, "no reader yet"))
            # optimistic: assume available, contribute nothing concrete to assign
            continue

        if spec.kind == "slot_lease":
            if c.amount > world.slots_free:
                blocks.append(Block(
                    c.resource, NO_FREE_SLOT,
                    f"need {c.amount}, free {world.slots_free}",
                ))
        elif spec.kind == "pool_regen":
            est = world.stamina_est
            if est is None or c.amount > est:
                have = "unread" if est is None else str(int(est))
                blocks.append(Block(
                    c.resource, INSUFFICIENT_STAMINA, f"need {c.amount}, have {have}",
                ))
        elif spec.kind == "typed_pool":
            avail = world.troops_for(c.type)
            if c.amount > avail:
                blocks.append(Block(
                    c.resource, NO_TROOPS, f"need {c.amount}, free {avail}",
                ))
            else:
                troops[c.type or "any"] = troops.get(c.type or "any", 0) + c.amount
        elif spec.kind == "exclusive_set":
            free = world.heroes_for(c.role)
            free = tuple(h for h in free if h not in heroes)   # don't double-book
            if len(free) < c.count:
                blocks.append(Block(
                    c.resource, NO_FREE_HERO,
                    f"need {c.count} {c.role or 'any'}, free {len(free)}",
                ))
            else:
                heroes.extend(free[: c.count])

    if blocks:
        return Affordability(ok=False, blocks=tuple(blocks))
    return Affordability(
        ok=True,
        assignment=Assignment(heroes=tuple(heroes), troops=dict(troops)),
    )
