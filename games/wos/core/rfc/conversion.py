"""Fire Crystal → Refined Fire Crystal weekly-conversion cost model (expected value).

RFC is produced by the weekly FC conversion system: up to 100 conversions/week across
5 tiers of 20, each tier a fixed FC cost returning a random RFC amount (distribution
in ``games/wos/db/rfc_conversion.yaml``, from wostools.net/rfc-simulator). Once per day
one conversion is 50% off.

This is the planning-useful half of the wostools *simulator*: expected-value math, NOT
RNG. It answers "how much FC (and how many weeks) to net N RFC?" — turning a furnace
RFC requirement into an FC budget + a time estimate. The efficient strategy is Tier-1
only (best RFC/FC), which the calculator uses for ``plan_for_rfc``; ``convert_path`` gives
the EV of pushing through higher tiers when FC is abundant.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/core/rfc/ → parents[2] = games/wos
DEFAULT_RFC_PATH = Path(__file__).resolve().parents[2] / "db" / "rfc_conversion.yaml"

WEEK_DAYS = 7   # one daily 50%-off conversion per day → 7 discounts/week


@dataclass(frozen=True, slots=True)
class RfcTier:
    tier: int
    lo: int                       # first weekly conversion index in this tier
    hi: int                       # last
    fc_cost: int                  # FC per conversion
    expected_rfc: float           # source's authoritative EV (handles the "6+" bucket)
    outcomes: Mapping[int, float] # rfc amount → probability (display distribution)


@dataclass(frozen=True, slots=True)
class RfcConversion:
    tiers: tuple[RfcTier, ...]
    weekly_cap: int
    tier_size: int
    daily_discount: float

    def tier_at(self, index: int) -> RfcTier:
        """Tier for weekly conversion ``index`` (0-based); clamps to the last tier."""
        for t in self.tiers:
            if t.lo <= index <= t.hi:
                return t
        return self.tiers[-1]


@lru_cache(maxsize=2)
def load_rfc_conversion(path: str | Path | None = None) -> RfcConversion:
    """Load ``rfc_conversion.yaml`` → :class:`RfcConversion` (cached)."""
    p = Path(path) if path else DEFAULT_RFC_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tiers = tuple(
        RfcTier(
            tier=int(t["tier"]), lo=int(t["lo"]), hi=int(t["hi"]),
            fc_cost=int(t["fc_cost"]), expected_rfc=float(t["expected_rfc"]),
            outcomes={int(k): float(v) for k, v in (t.get("outcomes") or {}).items()},
        )
        for t in (doc.get("tiers") or [])
    )
    return RfcConversion(
        tiers=tiers,
        weekly_cap=int(doc.get("weekly_cap", 100)),
        tier_size=int(doc.get("tier_size", 20)),
        daily_discount=float(doc.get("daily_discount", 0.5)),
    )


@dataclass(frozen=True, slots=True)
class ConvertPath:
    """Expected outcome of a run of conversions across tiers (no daily discount)."""

    conversions: int
    fc_spent: int
    expected_rfc: float
    fc_per_rfc: float
    by_tier: tuple[Mapping[str, float], ...]


def convert_path(
    conversions: int, start_index: int = 0, *, prep: RfcConversion | None = None
) -> ConvertPath:
    """EV of doing ``conversions`` conversions starting at weekly ``start_index``.

    Walks the tier ladder (cost + expected RFC per conversion), capped at ``weekly_cap``.
    No daily discount (raw tier cost) — that's the "push through tiers" view."""
    prep = prep or load_rfc_conversion()
    fc = 0
    rfc = 0.0
    done = 0
    per: dict[int, dict[str, float]] = {}
    idx = max(0, int(start_index))
    for _ in range(max(0, int(conversions))):
        if idx >= prep.weekly_cap:
            break
        t = prep.tier_at(idx)
        fc += t.fc_cost
        rfc += t.expected_rfc
        done += 1
        e = per.setdefault(t.tier, {"tier": t.tier, "conversions": 0, "fc": 0, "rfc": 0.0})
        e["conversions"] += 1
        e["fc"] += t.fc_cost
        e["rfc"] += t.expected_rfc
        idx += 1
    by_tier = tuple({**e, "rfc": round(e["rfc"], 2)} for e in per.values())
    return ConvertPath(
        conversions=done, fc_spent=fc, expected_rfc=round(rfc, 2),
        fc_per_rfc=round(fc / rfc, 2) if rfc else 0.0, by_tier=by_tier,
    )


@dataclass(frozen=True, slots=True)
class WeeklyEfficient:
    """The Tier-1-only "golden rule" weekly yield (best RFC/FC)."""

    conversions: int
    expected_rfc: float
    fc_no_discount: int
    fc_with_discount: int
    fc_per_rfc: float          # with the daily discount applied


def efficient_weekly(*, prep: RfcConversion | None = None) -> WeeklyEfficient:
    """One week of the efficient Tier-1 strategy (20 conversions, 7 daily discounts)."""
    prep = prep or load_rfc_conversion()
    t1 = prep.tiers[0]
    conv = prep.tier_size                       # 20 conversions, all within Tier 1
    rfc = conv * t1.expected_rfc
    fc_full = conv * t1.fc_cost
    discounts = min(conv, WEEK_DAYS)
    fc_disc = fc_full - discounts * int(t1.fc_cost * prep.daily_discount)
    return WeeklyEfficient(
        conversions=conv, expected_rfc=round(rfc, 2),
        fc_no_discount=fc_full, fc_with_discount=fc_disc,
        fc_per_rfc=round(fc_disc / rfc, 2) if rfc else 0.0,
    )


@dataclass(frozen=True, slots=True)
class RfcPlan:
    """FC + conversions + weeks to reach a target RFC via the efficient Tier-1 pace."""

    target_rfc: int
    conversions: int
    weeks: int
    fc_needed: int
    expected_rfc: float
    fc_per_rfc: float
    with_discount: bool


def plan_for_rfc(
    target_rfc: int, *, with_discount: bool = True, prep: RfcConversion | None = None
) -> RfcPlan:
    """FC budget + weeks to net ``target_rfc`` using Tier-1-only conversions.

    Tier 1 is the best RFC/FC ratio, so the efficient plan stays in Tier 1: each week
    does up to ``tier_size`` conversions (one per day gets the 50% discount). Returns the
    expected (not guaranteed) RFC for that many conversions."""
    prep = prep or load_rfc_conversion()
    t1 = prep.tiers[0]
    target = max(0, int(target_rfc))
    conv = ceil(target / t1.expected_rfc) if target else 0
    weeks = ceil(conv / prep.tier_size) if conv else 0
    fc_full = conv * t1.fc_cost
    if with_discount:
        discounts = min(conv, weeks * WEEK_DAYS)
        fc = fc_full - discounts * int(t1.fc_cost * prep.daily_discount)
    else:
        fc = fc_full
    rfc = round(conv * t1.expected_rfc, 2)
    return RfcPlan(
        target_rfc=target, conversions=conv, weeks=weeks, fc_needed=fc,
        expected_rfc=rfc, fc_per_rfc=round(fc / rfc, 2) if rfc else 0.0,
        with_discount=with_discount,
    )
