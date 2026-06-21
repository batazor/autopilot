"""Resolve ``{resource: spendable_amount}`` from a flat gamer state.

The CP-SAT solver enforces ``Σ cost[r] * x ≤ spendable[r]`` for each
resource we feed in. This module is the single source of truth for
mapping balance-config resource names to the corresponding state keys
(or hero-specific buckets when the resource is per-hero).

Until we have actual OCR for hero-XP / manuals / specific shards, most
buckets fall back to 0 — the solver will then simply prune those
candidates from the selection. That's a feature: with unknown
inventory we'd rather under-execute than over-spend.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from optimizer.context import BalanceContext

# Global resources whose spendable count lives at a fixed flat-state key
# (or a small list of fallbacks — first non-empty wins).
_GLOBAL_RESOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "hero_xp": ("resources.hero_xp", "heroes.hero_xp"),
    "gems": ("resources.diamond", "resources.gems"),
    "rare_expedition_manual": ("resources.rare_expedition_manual",),
    "epic_expedition_manual": ("resources.epic_expedition_manual",),
    "mythic_expedition_manual": ("resources.mythic_expedition_manual",),
    "rare_exploration_manual": ("resources.rare_exploration_manual",),
    "epic_exploration_manual": ("resources.epic_exploration_manual",),
    "mythic_exploration_manual": ("resources.mythic_exploration_manual",),
    "rare_general_shard": ("resources.rare_general_shard",),
    "epic_general_shard": ("resources.epic_general_shard",),
    "mythic_general_shard": ("resources.mythic_general_shard",),
}


def _int_at(state_flat: dict[str, Any], key: str) -> int | None:
    v = state_flat.get(key)
    if v is None:
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return None


def _resolve_global(state_flat: dict[str, Any], resource: str) -> int:
    for key in _GLOBAL_RESOURCE_KEYS.get(resource, ()):
        v = _int_at(state_flat, key)
        if v is not None:
            return v
    return 0


def _resolve_specific_shard(state_flat: dict[str, Any], resource: str) -> int:
    """``{hero_id}_specific_shard`` reads from
    ``heroes.entries.<hero_id>.shards_current`` (populated by
    ``scan_heroes_grid`` for locked cards). For unlocked heroes there's
    no badge to OCR; we fall back to 0 until a dedicated shard tracker
    exists."""
    if not resource.endswith("_specific_shard"):
        return 0
    # Resource shape from candidates is e.g. ``epic_specific_shard`` — by
    # rarity, NOT by hero id. The actual spend is per-hero though, so we
    # need the candidate to know which hero. capacity() below handles
    # that by passing hero_id. This helper handles the plain rarity-
    # named resource; treat as a pool summed across heroes for now.
    return 0


def gems_reserve_floor(ctx: BalanceContext) -> int:
    """Reserve floor pulled from the active profile's wheel policy.
    Subtracted from raw ``gems`` so the solver only sees spendable gems
    (mirrors ``reserve_gems_for_wheel`` from the progression plan)."""
    profile = ctx.active_profile
    wheel = str(profile.get("wheel_policy") or "")
    if wheel != "reserve_for_next_gen":
        return 0
    wheel_table = ctx.cost_tables.get("lucky_wheel_v2026_05") or {}
    try:
        return int(wheel_table.get("gems_per_10_spin") or 13500)
    except (TypeError, ValueError):
        return 13500


def compute_capacities(
    state_flat: dict[str, Any], ctx: BalanceContext
) -> dict[str, int]:
    """Return spendable amounts for every resource we currently track.

    Unknown resources default to 0 — the solver then refuses any
    candidate that requires them, which is the safe behaviour while
    OCR/state coverage is still being built out.
    """
    caps: dict[str, int] = {}
    for resource in _GLOBAL_RESOURCE_KEYS:
        caps[resource] = _resolve_global(state_flat, resource)

    # Per-hero shard buckets — match the ``{hero_id}_shard`` resource
    # the candidate generator emits for ``star_tier_up``. Reading from
    # ``heroes.entries.<hid>.shards_current`` (populated by
    # ``scan_heroes_grid`` for locked cards). Unlocked heroes don't have
    # this badge today, so bucket stays 0 until a dedicated stockpile
    # tracker lands.
    for key, v in state_flat.items():
        if not isinstance(key, str) or not key.startswith("heroes.entries."):
            continue
        if not key.endswith(".shards_current"):
            continue
        try:
            amount = max(0, int(v))
        except (TypeError, ValueError):
            continue
        hero_id = key.split(".", 3)[2]
        if not hero_id:
            continue
        caps[f"{hero_id}_shard"] = amount

    # Apply gems reserve floor: solver only sees spendable gems.
    if "gems" in caps:
        caps["gems"] = max(0, caps["gems"] - gems_reserve_floor(ctx))
    return caps


def _hero_rarity(hero_id: str) -> str:
    """Wiki rarity → optimizer naming (legendary → mythic)."""
    from optimizer.candidates import _RARITY_ALIAS, hero_db_entry  # local import: avoid cycle
    raw = str(hero_db_entry(hero_id).get("rarity") or "").strip().lower()
    if not raw:
        return ""
    return _RARITY_ALIAS.get(raw, raw)
