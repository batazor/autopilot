"""Score a candidate using the formula from ``deep-research-report.md``::

    mode_value(cmd, m)        = profile_weight(m) * hero_mode_weight(hero, m) *
                                upgrade_gain(cmd, m) * urgency_multiplier(cmd, m)
    base_value(cmd)           = Σ mode_value(cmd, m) for m in active_modes
    replacement_penalty(cmd)  = replacement_risk(hero, horizon) *
                                sunkness(action, resource_type) *
                                proximity_to_next_gen(server_age_days)
    resource_rarity_penalty   = Σ scarcity_weight(r) * cost_r / max(1, spendable_r)
    final_score(cmd)          = max(0, base_value - replacement_penalty - resource_rarity_penalty)

For MVP we deliberately keep ``upgrade_gain`` and ``urgency_multiplier`` at
constant 1.0 — the level table fed into the scorer is monotonic, so the
relative ranking is already controlled by profile × hero weights minus
resource penalty. Future iterations will fill those terms in with deltas
of game stats (Power/Attack/etc.) once the executor lands.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from optimizer.candidates import hero_db_entry
from optimizer.capacities import compute_capacities
from optimizer.context import BalanceContext
from optimizer.types import Candidate


@dataclass(frozen=True)
class ScoreBreakdown:
    base_value: float
    mode_contributions: dict[str, float]
    upgrade_gain: float
    threshold_bonus: float
    """Discrete jump applied on top of ``base_value`` — currently only the
    bear-join skill-5 threshold from ``defaults.threshold_bonuses``."""
    replacement_penalty: float
    replacement_risk: float
    sunkness: float
    resource_rarity_penalty: float
    resource_contributions: dict[str, float]
    final_score: float
    notes: tuple[str, ...] = field(default_factory=tuple)


_SUNKNESS_KEY_BY_ACTION: dict[str, str] = {
    "level_up": "level_up_pre_drill",
    "skill_up": "skill_up",
    "gear_enhance": "gear_enhance",
    "star_tier_up": "star_tier_up_specific_shards",
}

# Role tags that count as "joiner". The Bear-skill-5 threshold bonus is
# only applied when the hero is one of these *and* the action is the
# first expedition skill reaching level 5.
_BEAR_JOINER_TAGS = frozenset(
    {"joiner_only", "bear_joiner", "bear_specialist", "dual_role_support"}
)
"""Which ``defaults.sunkness`` entry covers each action. ``level_up`` is
pre-drill by default; we'll switch to ``level_up_post_drill`` once the
Drill Camp gate from state lands. ``star_tier_up`` defaults to the
specific-shard sunkness — the general-shard variant kicks in only when a
profile policy explicitly allows it (see Phase 2)."""

# Normalisation constant for level_up ``upgrade_gain``: typical adjacent-
# level Power delta in the sheet is ~21k for the early ladder. Dividing
# by it keeps the gain term in roughly [0.5, 2.0] for the levels we have
# data for, so it modulates rather than dwarfs the base value.
_LEVEL_UP_POWER_NORM = 21000.0
_LEVEL_UP_MIN_GAIN = 0.10


def _profile_weights(ctx: BalanceContext) -> dict[str, float]:
    raw = (ctx.active_profile.get("objective_weights") or {})
    return {str(k): float(v) for k, v in raw.items()}


def _hero_mode_weight(ctx: BalanceContext, hero_id: str | None, mode: str) -> float:
    if not hero_id:
        return 1.0
    meta = ctx.hero_meta(hero_id)
    mw = meta.get("mode_weights") or {}
    try:
        return float(mw.get(mode, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _replacement_risk(
    ctx: BalanceContext, hero_id: str | None, server_age_days: int
) -> float:
    if not hero_id:
        return 0.0
    curve = (ctx.hero_meta(hero_id).get("replacement_risk_curve") or {})
    if not isinstance(curve, dict) or not curve:
        return 0.0
    anchors: list[tuple[int, float]] = []
    for k, v in curve.items():
        try:
            anchors.append((int(k), float(v)))
        except (TypeError, ValueError):
            continue
    if not anchors:
        return 0.0
    anchors.sort(key=lambda kv: kv[0])
    if server_age_days <= anchors[0][0]:
        return anchors[0][1]
    if server_age_days >= anchors[-1][0]:
        return anchors[-1][1]
    for (d_a, r_a), (d_b, r_b) in itertools.pairwise(anchors):
        if d_a <= server_age_days <= d_b:
            span = d_b - d_a
            if span <= 0:
                return r_a
            t = (server_age_days - d_a) / span
            return r_a + (r_b - r_a) * t
    return anchors[-1][1]


def _sunkness(ctx: BalanceContext, action: str) -> float:
    key = _SUNKNESS_KEY_BY_ACTION.get(action, "")
    if not key:
        return 0.0
    try:
        return float((ctx.defaults.get("sunkness") or {}).get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _scarcity(ctx: BalanceContext, resource: str) -> float:
    try:
        return float((ctx.defaults.get("scarcity") or {}).get(resource, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _spendable(state_flat: dict[str, object], ctx: BalanceContext, resource: str) -> int:
    """Spendable amount for scoring, using the same resolver as CP-SAT."""
    return int(compute_capacities(state_flat, ctx).get(resource, 0))


def _upgrade_gain(c: Candidate) -> float:
    """How much value this specific upgrade adds, relative to a baseline
    of 1.0. MVP only models ``level_up`` via Power delta from the sheet;
    other action types fall back to 1.0 until we have stat tables for
    them (gear / star-tier / skill).
    """
    if c.action != "level_up" or not c.hero_id:
        return 1.0
    payload = c.payload or {}
    from_lv = payload.get("from_level")
    to_lv = payload.get("to_level")
    if not isinstance(from_lv, int) or not isinstance(to_lv, int):
        return 1.0
    table = (hero_db_entry(c.hero_id).get("levels") or {}).get("table") or {}
    cur = table.get(from_lv) or table.get(str(from_lv))
    nxt = table.get(to_lv) or table.get(str(to_lv))
    if not isinstance(cur, dict) or not isinstance(nxt, dict):
        return 1.0  # level > 10 or hero outside the sheet
    try:
        delta = int(nxt.get("power", 0)) - int(cur.get("power", 0))
    except (TypeError, ValueError):
        return 1.0
    if delta <= 0:
        return 1.0
    return max(_LEVEL_UP_MIN_GAIN, delta / _LEVEL_UP_POWER_NORM)


def _threshold_bonus(c: Candidate, ctx: BalanceContext) -> tuple[float, str]:
    """Discrete jumps applied on top of base. Returns ``(amount, reason)``.

    Currently only ``bear_join_skill_5`` is wired: a joiner-tagged hero
    pushing their first expedition skill to level 5 gets the bonus,
    because that's the breakpoint that activates a meaningful rally buff.
    """
    bonuses = (ctx.defaults.get("threshold_bonuses") or {})
    if c.action != "skill_up" or not c.hero_id:
        return 0.0, ""
    payload = c.payload or {}
    if payload.get("track") != "expedition":
        return 0.0, ""
    if int(payload.get("slot") or 0) != 1:
        return 0.0, ""
    if int(payload.get("to_level") or 0) != 5:
        return 0.0, ""
    meta = ctx.hero_meta(c.hero_id)
    tags = set(meta.get("role_tags") or [])
    if not (tags & _BEAR_JOINER_TAGS):
        return 0.0, ""
    try:
        amount = float(bonuses.get("bear_join_skill_5", 0.0))
    except (TypeError, ValueError):
        amount = 0.0
    return amount, "bear_join_skill_5"


def score_candidate(
    c: Candidate,
    ctx: BalanceContext,
    state_flat: dict[str, object],
    *,
    server_age_days: int = 0,
) -> ScoreBreakdown:
    """Apply the formula from the deep-research-report to one candidate."""
    profile = _profile_weights(ctx)
    upgrade_gain = _upgrade_gain(c)
    mode_contrib: dict[str, float] = {}
    for mode, pw in profile.items():
        hw = _hero_mode_weight(ctx, c.hero_id, mode)
        mv = pw * hw * upgrade_gain  # urgency_multiplier == 1.0 in MVP
        if mv:
            mode_contrib[mode] = mv

    threshold_bonus, threshold_reason = _threshold_bonus(c, ctx)
    base = sum(mode_contrib.values()) + threshold_bonus

    replacement_risk = _replacement_risk(ctx, c.hero_id, server_age_days)
    sunkness = _sunkness(ctx, c.action)
    replacement_penalty = replacement_risk * sunkness * base  # scale to magnitude

    rarity_contrib: dict[str, float] = {}
    rarity_total = 0.0
    for cost in c.costs:
        sc = _scarcity(ctx, cost.resource)
        spendable = _spendable(state_flat, ctx, cost.resource)
        # When spendable is unknown / zero we still want to penalise high
        # costs — denominator clamps at 1 so the term stays finite.
        denom = max(1.0, float(spendable))
        share = sc * float(cost.amount) / denom
        # Scale the share into the same range as ``base`` so the subtraction
        # makes sense. Without this hero_xp ~ 10k would overwhelm any base
        # value < 100. The 0.5 factor gives the rarity term room to bite
        # but not eclipse the base ranking.
        weighted = share * base * 0.5
        if weighted:
            rarity_contrib[cost.resource] = weighted
            rarity_total += weighted

    final = max(0.0, base - replacement_penalty - rarity_total)
    notes: list[str] = []
    if base == 0.0:
        notes.append("hero has no mode_weights for active profile")
    if not c.costs:
        notes.append("no cost defined — only ranked by base value")
    if upgrade_gain == 1.0 and c.action == "level_up":
        notes.append("upgrade_gain fallback (no level table or beyond Lv10)")
    if threshold_reason:
        notes.append(f"threshold_bonus: {threshold_reason} (+{int(threshold_bonus)})")
    return ScoreBreakdown(
        base_value=base,
        mode_contributions=mode_contrib,
        upgrade_gain=upgrade_gain,
        threshold_bonus=threshold_bonus,
        replacement_penalty=replacement_penalty,
        replacement_risk=replacement_risk,
        sunkness=sunkness,
        resource_rarity_penalty=rarity_total,
        resource_contributions=rarity_contrib,
        final_score=final,
        notes=tuple(notes),
    )
