"""Pure data model + math for the shared stamina budget.

No Redis, no ADB, no game IO — every function here is deterministic and unit
testable. The Redis-backed adapter (added separately) resolves live game state
into the snapshots that :mod:`allocator` consumes.

Resource recap: a single per-account "stamina" pool, capped (overflow burns),
regenerating at a fixed rate, shared by several consumers (intel events, Joe
bandit hunts, beast hunting). ``budget.yaml`` holds the demand table; the
allocation policy lives in :mod:`allocator`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CAP = 200
DEFAULT_REGEN_PER_HOUR = 10.0
_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_BUDGET_PATH = _MODULE_DIR / "budget.yaml"


def _as_opt_int(value: Any) -> int | None:
    """Coerce to int, mapping missing/empty/``null`` to ``None`` (unlimited)."""
    if value is None or value == "":
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class Demand:
    """One stamina consumer declared in ``budget.yaml``."""

    id: str
    task_type: str
    priority: int
    cost: int
    daily_quota: int | None = None   # None → unlimited (overflow sink)
    active_when: str | None = None   # python-expr cond; None → always active
    reserve_floor: int = 0           # stamina held back for this demand

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Demand:
        return cls(
            id=str(raw["id"]),
            task_type=str(raw.get("task_type") or raw["id"]),
            priority=int(raw.get("priority", 0)),
            cost=int(raw.get("cost", 0)),
            daily_quota=_as_opt_int(raw.get("daily_quota")),
            active_when=(str(raw["active_when"]) if raw.get("active_when") else None),
            reserve_floor=int(raw.get("reserve_floor", 0)),
        )


@dataclass(frozen=True, slots=True)
class Supply:
    """A stamina source (e.g. a pet skill) the allocator can trigger."""

    id: str
    task_type: str
    gives: int
    daily_quota: int | None = None
    trigger_when: str | None = None   # python-expr cond; None → always armed

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Supply:
        return cls(
            id=str(raw["id"]),
            task_type=str(raw.get("task_type") or raw["id"]),
            gives=int(raw.get("gives", 0)),
            daily_quota=_as_opt_int(raw.get("daily_quota")),
            trigger_when=(str(raw["trigger_when"]) if raw.get("trigger_when") else None),
        )


@dataclass(frozen=True, slots=True)
class Budget:
    """Parsed ``budget.yaml`` — the declarative demand table for one game."""

    cap: int = DEFAULT_CAP
    regen_per_hour: float = DEFAULT_REGEN_PER_HOUR
    daily_reset_utc: str = "00:00"   # in-game daily reset (HH:MM UTC), NOT midnight
    overflow_horizon_hours: float = 1.0  # cap within this window → drain (anti-burn)
    enabled: bool = False            # OFF until consumer scenarios + OCR region exist
    demands: tuple[Demand, ...] = ()
    supplies: tuple[Supply, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Budget:
        raw = raw or {}
        return cls(
            cap=int(raw.get("cap", DEFAULT_CAP)),
            regen_per_hour=float(raw.get("regen_per_hour", DEFAULT_REGEN_PER_HOUR)),
            daily_reset_utc=str(raw.get("daily_reset_utc", "00:00")),
            overflow_horizon_hours=float(raw.get("overflow_horizon_hours", 1.0)),
            enabled=bool(raw.get("enabled", False)),
            demands=tuple(Demand.from_dict(d) for d in (raw.get("demands") or [])),
            supplies=tuple(Supply.from_dict(s) for s in (raw.get("supplies") or [])),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> Budget:
        p = Path(path) if path else DEFAULT_BUDGET_PATH
        return cls.from_dict(yaml.safe_load(p.read_text(encoding="utf-8")))

    def demand(self, demand_id: str) -> Demand | None:
        return next((d for d in self.demands if d.id == demand_id), None)


def estimate_stamina(
    last_value: float | None,
    read_at: float,
    now: float,
    *,
    cap: int,
    regen_per_hour: float,
) -> float | None:
    """Interpolate stamina between OCR reads.

    ``last_value`` / ``read_at`` come from the most recent on-screen read; the
    result is regen-adjusted to ``now`` and clamped to ``cap``. A fresh OCR read
    always overwrites this estimate upstream — interpolation is only a bridge.
    Returns ``None`` when there is no prior reading (caller must read first).
    """
    if last_value is None:
        return None
    elapsed = max(0.0, float(now) - float(read_at))
    regained = (float(regen_per_hour) / 3600.0) * elapsed
    return min(float(cap), float(last_value) + regained)


def seconds_to_cap(est: float | None, *, cap: int, regen_per_hour: float) -> float:
    """Seconds until the pool reaches ``cap`` (and starts burning). ``inf`` if
    regen is non-positive; ``0.0`` if already at/over cap or estimate missing."""
    if est is None:
        return math.inf
    if est >= cap:
        return 0.0
    if regen_per_hour <= 0:
        return math.inf
    return (cap - est) / (float(regen_per_hour) / 3600.0)


def seconds_to_afford(est: float | None, cost: float, *, regen_per_hour: float) -> float:
    """Seconds until ``est`` regenerates up to ``cost`` (0 if already affordable).

    ``inf`` when there's no estimate or regen is non-positive. Lets callers set
    a sensible TTL/back-off instead of re-polling every tick — e.g. at 1 point /
    5 min, waiting for a 10-cost action that's 3 short means ~15 min, not 30 s.
    """
    if est is None:
        return math.inf
    if est >= cost:
        return 0.0
    if regen_per_hour <= 0:
        return math.inf
    return (float(cost) - float(est)) / (float(regen_per_hour) / 3600.0)


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "00:00").split(":")
    h = int(parts[0]) if parts and parts[0] != "" else 0
    m = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
    return h % 24, m % 60


def quota_period(now: float, daily_reset_utc: str = "00:00") -> str:
    """Game-day key (``YYYYMMDD``) for ``now``, shifted by the in-game reset.

    Quotas reset at ``daily_reset_utc`` (not midnight UTC): an action taken
    before the reset hour still counts toward the previous game-day.
    """
    h, m = _parse_hhmm(daily_reset_utc)
    dt = datetime.fromtimestamp(float(now), tz=UTC) - timedelta(hours=h, minutes=m)
    return dt.strftime("%Y%m%d")


def quota_field(period: str, demand_id: str) -> str:
    """Redis hash field for a demand's per-day counter (adapter writes here)."""
    return f"quota:{period}:{demand_id}"


def is_active(demand: Demand, context: dict[str, Any]) -> bool:
    """Resolve a demand's ``active_when`` against a flat state ``context``.

    No condition → always active. Reuses :func:`layout.area_versions.eval_cond`
    (lazy-imported to keep this module's import graph light), so conditions are
    written in Python-expression syntax (``and`` / ``or``, not SQL ``AND``).
    """
    if not demand.active_when:
        return True
    return _eval(demand.active_when, context)


def is_triggered(supply: Supply, context: dict[str, Any]) -> bool:
    """Resolve a supply's ``trigger_when`` against a flat state ``context``."""
    if not supply.trigger_when:
        return True
    return _eval(supply.trigger_when, context)


def _eval(expr: str, context: dict[str, Any]) -> bool:
    from layout.area_versions import eval_cond

    return eval_cond(expr, dict(context))
