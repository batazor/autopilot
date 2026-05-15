"""Generate next-step upgrade candidates from a gamer's flat state.

MVP scope: ``level_up`` and ``star_tier_up`` — one candidate per
unlocked hero, pointing at the very next legal level / star-tier
slot. Future milestones add ``skill_up`` / ``gear_assign``; each
follows the same shape so the scorer doesn't have to know about
action specifics.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from optimizer.context import BalanceContext
from optimizer.types import Candidate, Cost

_DEFAULT_HERO_LEVEL_CAP = 80
_HERO_XP_TABLE = "hero_xp_v1"
_FURNACE_GATE_TABLE = "hero_level_cap_by_furnace_v1"
_STAR_TIERS_PER_STAR = 6
_MAX_STAR_LEVEL = 5
_MAX_STAR_PROGRESS = _STAR_TIERS_PER_STAR * _MAX_STAR_LEVEL  # 30

_SKILL_TRACKS = ("expedition", "exploration")
_SKILL_MANUAL_TABLE = "skill_manual_costs_v1"
_SKILL_CAP_TABLE = "skill_level_cap_by_star_v1"
_SKILL_MAX_LEVEL = 5

# Wiki uses "Legendary" for the top rarity; the optimizer / balance configs
# use "mythic" (matches the deep-research-report's vocabulary and the
# scarcity keys in defaults.yaml). Normalise once so downstream consumers
# don't have to special-case either spelling.
_RARITY_ALIAS: dict[str, str] = {
    "legendary": "mythic",
    "myth": "mythic",
}


def generate_candidates(
    state_flat: dict[str, object],
    ctx: BalanceContext,
) -> list[Candidate]:
    """Walk ``heroes.entries.*`` in the flat state and produce one
    candidate per (hero, action) whose next step is legal & costed."""
    out: list[Candidate] = []
    out.extend(_generate_level_up(state_flat, ctx))
    out.extend(_generate_star_tier_up(state_flat, ctx))
    out.extend(_generate_skill_up(state_flat, ctx))
    return out


@lru_cache(maxsize=128)
def hero_db_entry(hero_id: str) -> dict[str, Any]:
    """Per-hero wiki YAML parsed — cached so candidate gen and scorer can share."""
    if not hero_id:
        return {}
    from config.heroes import hero_yaml_path

    path = hero_yaml_path(hero_id)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# level_up
# ---------------------------------------------------------------------------


def _hero_ids_from_state(state_flat: dict[str, object]) -> set[str]:
    """Hero ids that have any ``heroes.entries.<id>.<field>`` key in the
    flat state. We also include heroes flagged ``available=True`` even
    when no other fields are set."""
    seen: set[str] = set()
    for key in state_flat:
        if not isinstance(key, str):
            continue
        if not key.startswith("heroes.entries."):
            continue
        rest = key[len("heroes.entries."):]
        hid = rest.split(".", 1)[0].strip()
        if hid:
            seen.add(hid)
    return seen


def _hero_level(state_flat: dict[str, object], hid: str) -> int:
    v = state_flat.get(f"heroes.entries.{hid}.level")
    try:
        return int(v) if v is not None else 1
    except (TypeError, ValueError):
        return 1


def _hero_is_available(state_flat: dict[str, object], hid: str) -> bool:
    v = state_flat.get(f"heroes.entries.{hid}.available")
    if isinstance(v, bool):
        return v
    return bool(v)


def _furnace_level(state_flat: dict[str, object]) -> int:
    for key in ("chief.furnace_level", "furnace.level", "buildings.furnace.level"):
        v = state_flat.get(key)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            continue
    return 0


def _hero_level_cap(ctx: BalanceContext, furnace_level: int) -> int:
    table = ctx.cost_tables.get(_FURNACE_GATE_TABLE) or {}
    if not isinstance(table, dict):
        return _DEFAULT_HERO_LEVEL_CAP
    best_cap = 0
    for f_lv, cap in table.items():
        try:
            f_lv_i = int(f_lv)
            cap_i = int(cap)
        except (TypeError, ValueError):
            continue
        if furnace_level >= f_lv_i and cap_i > best_cap:
            best_cap = cap_i
    return best_cap or _DEFAULT_HERO_LEVEL_CAP


def _interpolate_xp(table: dict[Any, Any], target_level: int) -> int:
    """``hero_xp_v1.per_level`` is sparse (key levels every 10). For levels
    in between we linearly interpolate between the nearest anchors so the
    scorer has a defined cost for every level — good enough for an MVP."""
    if target_level <= 1:
        return 0
    anchors: list[tuple[int, int]] = []
    for k, v in table.items():
        try:
            anchors.append((int(k), int(v)))
        except (TypeError, ValueError):
            continue
    if not anchors:
        return 0
    anchors.sort(key=lambda kv: kv[0])
    # Exact match?
    for lv, cost in anchors:
        if lv == target_level:
            return cost
    # Below first anchor → use it.
    if target_level <= anchors[0][0]:
        return anchors[0][1]
    # Above last anchor → use it.
    if target_level >= anchors[-1][0]:
        return anchors[-1][1]
    # Linear interpolation between bracketing anchors.
    for (lv_a, cost_a), (lv_b, cost_b) in zip(anchors, anchors[1:], strict=False):
        if lv_a <= target_level <= lv_b:
            span = lv_b - lv_a
            if span <= 0:
                return cost_a
            t = (target_level - lv_a) / span
            return int(round(cost_a + (cost_b - cost_a) * t))
    return anchors[-1][1]


def _hero_star_progress(state_flat: dict[str, object], hid: str) -> int:
    v = state_flat.get(f"heroes.entries.{hid}.star_progress")
    try:
        if v is None:
            return 0
        return max(0, min(_MAX_STAR_PROGRESS, int(v)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# star_tier_up
# ---------------------------------------------------------------------------


def _generate_star_tier_up(
    state_flat: dict[str, object], ctx: BalanceContext
) -> list[Candidate]:
    """One ``star_tier_up`` candidate per unlocked hero, sized at the
    very next slot of the 5★ × 6-tier path (see the ``shards`` table in
    each ``db/heroes/<id>.yaml``)."""
    out: list[Candidate] = []
    for hid in sorted(_hero_ids_from_state(state_flat)):
        if not _hero_is_available(state_flat, hid):
            continue
        progress = _hero_star_progress(state_flat, hid)
        if progress >= _MAX_STAR_PROGRESS:
            continue
        hero = hero_db_entry(hid)
        if not hero:
            continue
        rows = ((hero.get("shards") or {}).get("rows") or [])
        if not isinstance(rows, list):
            continue
        star_idx = progress // _STAR_TIERS_PER_STAR
        tier_idx = progress % _STAR_TIERS_PER_STAR
        if star_idx >= len(rows):
            continue
        row = rows[star_idx]
        if not isinstance(row, dict):
            continue
        col_key = f"Tier {tier_idx + 1}"
        try:
            cost_amount = int(str(row.get(col_key) or "").strip())
        except (TypeError, ValueError):
            continue
        if cost_amount <= 0:
            continue
        raw_rarity = str(hero.get("rarity") or "").strip().lower() or "epic"
        rarity = _RARITY_ALIAS.get(raw_rarity, raw_rarity)
        # Per-hero shard bucket — matches ``heroes.entries.<hid>.shards_current``
        # in capacities.py. Rarity is preserved in the payload so a future
        # rule (e.g. ``general_shard_fallback``) can decide when to mix in
        # the general pool.
        resource = f"{hid}_shard"
        out.append(
            Candidate(
                id=f"star_tier_up:{hid}:{progress}->{progress + 1}",
                action="star_tier_up",
                hero_id=hid,
                priority_band="long_term_core",
                costs=(Cost(resource=resource, amount=cost_amount),),
                preconditions=(
                    f"hero {hid} unlocked",
                    f"star_progress {progress} < {_MAX_STAR_PROGRESS}",
                ),
                payload={
                    "from_progress": progress,
                    "to_progress": progress + 1,
                    "star_level": star_idx + 1,
                    "tier_in_star": tier_idx + 1,
                    "rarity": rarity,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# skill_up
# ---------------------------------------------------------------------------


def _hero_skill_level(
    state_flat: dict[str, object], hid: str, track: str, slot: int
) -> int:
    """Current level of ``heroes.entries.<hid>.skills.<track>.<slot>``.
    Returns 0 (= unlearned) when missing."""
    for key in (
        f"heroes.entries.{hid}.skills.{track}.{slot}",
        f"heroes.entries.{hid}.skills.{track}.slot_{slot}",
    ):
        v = state_flat.get(key)
        if v is None:
            continue
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            continue
    return 0


def _skill_level_cap(ctx: BalanceContext, star_progress: int) -> int:
    """Map ``star_progress`` (0..30) → current ★ level (0..5) → max skill lv."""
    star_level = min(_MAX_STAR_LEVEL, star_progress // _STAR_TIERS_PER_STAR)
    table = ctx.cost_tables.get(_SKILL_CAP_TABLE) or {}
    if not isinstance(table, dict):
        return _SKILL_MAX_LEVEL
    try:
        return int(table.get(star_level, _SKILL_MAX_LEVEL))
    except (TypeError, ValueError):
        return _SKILL_MAX_LEVEL


def _skill_priority_slots(meta: dict[str, Any], track: str) -> list[int]:
    sp = meta.get("skill_priority") or {}
    if not isinstance(sp, dict):
        return []
    raw = sp.get(track) or []
    if not isinstance(raw, list):
        return []
    slots: list[int] = []
    for v in raw:
        try:
            slots.append(int(v))
        except (TypeError, ValueError):
            continue
    return slots


def _skill_manual_cost(
    ctx: BalanceContext, rarity: str, track: str, from_level: int
) -> int | None:
    table = ctx.cost_tables.get(_SKILL_MANUAL_TABLE) or {}
    if not isinstance(table, dict):
        return None
    by_rarity = table.get(rarity) or {}
    if not isinstance(by_rarity, dict):
        return None
    by_track = by_rarity.get(track) or {}
    if not isinstance(by_track, dict):
        return None
    raw = by_track.get(from_level)
    if raw is None:
        raw = by_track.get(str(from_level))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _generate_skill_up(
    state_flat: dict[str, object], ctx: BalanceContext
) -> list[Candidate]:
    """One ``skill_up`` candidate per (hero, track, slot) listed in the
    hero's ``skill_priority``. Caps at the per-★ skill-level cap so the
    optimizer never proposes an illegal advance."""
    out: list[Candidate] = []
    for hid in sorted(_hero_ids_from_state(state_flat)):
        if not _hero_is_available(state_flat, hid):
            continue
        hero = hero_db_entry(hid)
        if not hero:
            continue
        raw_rarity = str(hero.get("rarity") or "").strip().lower() or "epic"
        rarity = _RARITY_ALIAS.get(raw_rarity, raw_rarity)
        meta = ctx.hero_meta(hid)
        cap = _skill_level_cap(ctx, _hero_star_progress(state_flat, hid))
        for track in _SKILL_TRACKS:
            for slot in _skill_priority_slots(meta, track):
                cur = _hero_skill_level(state_flat, hid, track, slot)
                nxt = cur + 1
                if nxt > cap or nxt > _SKILL_MAX_LEVEL:
                    continue
                cost_amount = _skill_manual_cost(ctx, rarity, track, cur)
                if cost_amount is None or cost_amount <= 0:
                    continue
                resource = f"{rarity}_{track}_manual"
                out.append(
                    Candidate(
                        id=f"skill_up:{hid}:{track}.{slot}:{cur}->{nxt}",
                        action="skill_up",
                        hero_id=hid,
                        priority_band=(
                            "threshold" if nxt == 5 and track == "expedition" and slot == 1
                            else "core"
                        ),
                        costs=(Cost(resource=resource, amount=cost_amount),),
                        preconditions=(
                            f"hero {hid} unlocked",
                            f"skill {track}.{slot} level {cur} < cap ({cap})",
                        ),
                        payload={
                            "track": track,
                            "slot": slot,
                            "from_level": cur,
                            "to_level": nxt,
                            "rarity": rarity,
                        },
                    )
                )
    return out


def _generate_level_up(
    state_flat: dict[str, object], ctx: BalanceContext
) -> list[Candidate]:
    hero_xp_table = (
        (ctx.cost_tables.get(_HERO_XP_TABLE) or {}).get("per_level") or {}
    )
    if not isinstance(hero_xp_table, dict):
        return []
    cap = _hero_level_cap(ctx, _furnace_level(state_flat))
    out: list[Candidate] = []
    for hid in sorted(_hero_ids_from_state(state_flat)):
        if not _hero_is_available(state_flat, hid):
            continue
        cur = _hero_level(state_flat, hid)
        nxt = cur + 1
        if nxt > cap:
            continue
        xp = _interpolate_xp(hero_xp_table, nxt)
        if xp <= 0:
            continue
        out.append(
            Candidate(
                id=f"level_up:{hid}:{cur}->{nxt}",
                action="level_up",
                hero_id=hid,
                priority_band="core",
                costs=(Cost(resource="hero_xp", amount=xp),),
                preconditions=(
                    f"hero {hid} unlocked",
                    f"level < cap ({cur} < {cap})",
                ),
                payload={"from_level": cur, "to_level": nxt},
            )
        )
    return out
