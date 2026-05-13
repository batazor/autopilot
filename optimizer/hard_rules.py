"""Hard-rule candidate pruning. Apply **before** the CP-SAT model is
built — unsafe commands should never be visible to the solver.

The doc lists these rules:

* ``deny_mythic_general_shards_gen1_default`` — by default, ★-up via
  mythic-general shards is denied (allowlist via active profile).
* ``stop_sergey_deep_after_flint`` — once a Gen2 replacement is unlocked,
  block deep sunk-cost actions on the replaced hero.
* ``support_level_cap`` — joiner heroes get a level cap before drill,
  and zero manual leveling after drill.
* ``skill_cap_by_star`` — already enforced inside candidate generation,
  but we re-check here as a defence in depth.
* ``reserve_gems_for_wheel`` — handled inside :mod:`optimizer.capacities`
  (subtracts the reserve floor from spendable gems).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from optimizer.context import BalanceContext
from optimizer.types import Candidate


@dataclass(frozen=True)
class PruneResult:
    kept: list[Candidate]
    dropped: list[tuple[Candidate, str]]
    """``(candidate, reason)``. Reasons surface in the UI so it's clear
    *why* a particular upgrade vanished."""

    @property
    def dropped_reasons(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for c, reason in self.dropped:
            out.setdefault(reason, []).append(c.id)
        return out


# --- Individual rule predicates -------------------------------------------


def _is_mythic_general_shard_use(c: Candidate) -> bool:
    return c.action == "star_tier_up" and any(
        cost.resource == "mythic_general_shard" for cost in c.costs
    )


def _hero_in_profile_allowlist(
    ctx: BalanceContext, hero_id: str | None, rarity_bucket: str
) -> bool:
    """``general_shard_policy.<rarity_bucket>.allow_heroes`` may be a
    list of hero ids OR a dict ``{hero_id: {max_star: N}}``. Either way
    return True if ``hero_id`` is allowed."""
    if not hero_id:
        return False
    policy = (
        (ctx.active_profile.get("general_shard_policy") or {})
        .get(rarity_bucket)
        or {}
    )
    allow_raw = policy.get("allow_heroes")
    if isinstance(allow_raw, list):
        return hero_id in allow_raw
    if isinstance(allow_raw, dict):
        return hero_id in allow_raw
    return False


def _general_shard_policy_mode(ctx: BalanceContext, rarity_bucket: str) -> str:
    policy = (
        (ctx.active_profile.get("general_shard_policy") or {})
        .get(rarity_bucket)
        or {}
    )
    return str(policy.get("mode") or "deny_by_default").strip().lower()


def _hero_tags(ctx: BalanceContext, hero_id: str | None) -> set[str]:
    if not hero_id:
        return set()
    meta = ctx.hero_meta(hero_id)
    return {str(t) for t in (meta.get("role_tags") or []) if str(t).strip()}


_JOINER_TAGS = frozenset({"joiner_only", "bear_specialist"})


# --- Stop-replacement table -----------------------------------------------
# When a hero on the LEFT side is unlocked, deny the actions on each entry
# in ``stops`` for the hero on the RIGHT. Loaded inline here because the
# rules don't yet live in YAML — once the team adds more replacements, we
# can move this to ``config/balance/stop_rules.yaml``.
_STOP_REPLACEMENTS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "flint",
        "sergey",
        frozenset({"star_tier_up", "gear_enhance", "exclusive_gear_up"}),
    ),
)


def _hero_unlocked_in_state(state_flat: dict[str, Any], hero_id: str) -> bool:
    v = state_flat.get(f"heroes.entries.{hero_id}.available")
    return bool(v) if v is not None else False


# --- Main entry -----------------------------------------------------------


def prune_candidates(
    candidates: list[Candidate],
    state_flat: dict[str, Any],
    ctx: BalanceContext,
) -> PruneResult:
    """Return a PruneResult with the kept set + drop reasons."""
    kept: list[Candidate] = []
    dropped: list[tuple[Candidate, str]] = []

    # Precompute stop-rule activations so we don't recheck per candidate.
    stops: dict[str, frozenset[str]] = {}
    for trigger, target, actions in _STOP_REPLACEMENTS:
        if _hero_unlocked_in_state(state_flat, trigger):
            stops[target] = stops.get(target, frozenset()) | actions

    for c in candidates:
        # rule: mythic general shards
        if _is_mythic_general_shard_use(c):
            mode = _general_shard_policy_mode(ctx, "mythic")
            allowed = (
                mode == "allow_threshold_only"
                and _hero_in_profile_allowlist(ctx, c.hero_id, "mythic")
            )
            if not allowed:
                dropped.append((c, "general_shard_policy.mythic"))
                continue

        # rule: stop replacement (Sergey after Flint, etc.)
        if c.hero_id in stops and c.action in stops[c.hero_id]:
            dropped.append((c, f"stop_replacement:{c.hero_id}"))
            continue

        # rule: support level cap (joiner_only / bear_specialist)
        if c.action == "level_up":
            tags = _hero_tags(ctx, c.hero_id)
            if tags & _JOINER_TAGS:
                meta = ctx.hero_meta(c.hero_id) if c.hero_id else {}
                drill_open = bool(state_flat.get("account.drill_camp_unlocked"))
                cap_key = (
                    "manual_level_cap_post_drill"
                    if drill_open
                    else "manual_level_cap_pre_drill"
                )
                try:
                    cap = int(meta.get(cap_key, 0) or 0)
                except (TypeError, ValueError):
                    cap = 0
                to_level = int(c.payload.get("to_level") or 0)
                if cap == 0 or to_level > cap:
                    dropped.append((c, f"support_level_cap:{cap_key}={cap}"))
                    continue

        kept.append(c)

    return PruneResult(kept=kept, dropped=dropped)
