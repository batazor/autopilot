"""Human-readable reason codes for upgrade candidates.

The scorer emits a numeric breakdown; UI / explainability surfaces want
short labels like ``"active_core_marksman"`` or ``"bear_joiner_threshold"``.
This module derives them from the candidate, its breakdown, and the
balance context — same inputs the scorer sees, so the labels never lie
about the actual scoring path.

Codes are stable strings (no scoring): downstream consumers can pin
expectations in tests without depending on float magnitudes. Add new
codes by extending :func:`generate_reasons` — keep them short, lowercase,
underscore-separated; one code = one observable game-mechanic fact.
"""

from __future__ import annotations

from optimizer.context import BalanceContext
from optimizer.scorer import ScoreBreakdown
from optimizer.types import Candidate

_CORE_TAGS = {"core", "exploration_carry", "arena_carry", "expedition_frontline", "expedition_dps"}
_BEAR_JOINER_TAGS = {"joiner_only", "bear_joiner", "bear_specialist", "dual_role_support"}
_WHEEL_TAGS = {"wheel_path", "gen1_wheel"}
_GEN2_TAGS = {"gen2_replacement_carry", "gen2_replacement"}


def generate_reasons(
    c: Candidate,
    br: ScoreBreakdown,
    ctx: BalanceContext,
    *,
    is_selected: bool | None = None,
) -> list[str]:
    """Return ordered reason codes for ``c``. Empty list is valid.

    ``is_selected`` is optional context for negation codes
    (``solver_rejected_lower_score`` etc.) — pass it when known so the
    UI can render selected and rejected rows from the same list.
    """
    out: list[str] = []

    if c.hero_id:
        meta = ctx.hero_meta(c.hero_id)
        tags = {str(t).strip() for t in (meta.get("role_tags") or [])}
        if tags & _CORE_TAGS:
            out.append("active_core_lineup")
        if tags & _BEAR_JOINER_TAGS:
            out.append("bear_joiner_hero")
        if tags & _WHEEL_TAGS:
            out.append("wheel_path_hero")
        if tags & _GEN2_TAGS:
            out.append("gen2_replacement_hero")

    # Action-shape codes
    if c.action == "level_up":
        out.append("incremental_level")
    elif c.action == "star_tier_up":
        out.append("star_progression")
        rarity = (c.payload or {}).get("rarity")
        if rarity:
            out.append(f"rarity_{rarity}")
    elif c.action == "skill_up":
        out.append("skill_progression")
        track = (c.payload or {}).get("track")
        slot = (c.payload or {}).get("slot")
        if track:
            out.append(f"track_{track}")
        if (
            track == "expedition"
            and int(slot or 0) == 1
            and int((c.payload or {}).get("to_level") or 0) == 5
        ):
            out.append("bear_joiner_threshold")

    # Scorer-derived codes
    if br.threshold_bonus > 0:
        out.append("threshold_bonus_applied")
    if br.upgrade_gain >= 1.20:
        out.append("high_stat_gain")
    if br.base_value > 0 and br.replacement_penalty > br.base_value * 0.25:
        out.append("near_gen_rollover")
    if br.base_value > 0 and br.resource_rarity_penalty < br.base_value * 0.05:
        out.append("affordable")
    if br.base_value > 0 and br.resource_rarity_penalty > br.base_value * 0.50:
        out.append("expensive_for_budget")
    if br.final_score == 0 and br.base_value > 0:
        out.append("starved_by_resource_penalty")

    if is_selected is True:
        out.append("solver_selected")
    elif is_selected is False:
        out.append("solver_dropped")

    return out


def rejection_reason(
    c: Candidate,
    br: ScoreBreakdown | None,
    *,
    pruned_reason: str | None = None,
) -> str:
    """One-line label for why a candidate ended up not selected.

    ``pruned_reason`` from the hard-rule pass wins; otherwise we fall
    back to a scorer-derived reason (resource starvation / low score).
    """
    if pruned_reason:
        return pruned_reason
    if br is None:
        return "unknown"
    if br.final_score <= 0 and br.base_value > 0:
        return "starved_by_resource_penalty"
    if br.base_value <= 0:
        return "no_mode_weight_for_active_profile"
    return "lower_score_than_selected"
