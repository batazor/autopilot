"""Arena of Glory lineup optimizer — place 5 heroes against a known enemy lineup.

The arena fight is **automatic**: once started you cannot steer it, so the whole
decision is *which heroes go in which of the 5 seats*. This module is the pure
"given my heroes and the opponent's lineup, what is the best arrangement?" solver
the ``/arena`` page and ``/api/planner/arena`` call.

What actually decides an arena seat (from the community guides, cross-checked 2026-06,
encoded as tunable data in ``games/wos/db/arena_combat.yaml``):

* **5 seats.** Front = slots 1 & 5 (engaged first → tanks / crowd control). Back =
  slots 2, 3, 4 (damage / support). **Slot 4** is the safest seat and the only one
  that can reach all five enemies → the carry's seat.
* **Targeting** decides which enemies a seat engages ("slot 1 faces 2, 5, 4, 3"),
  which is what the class-counter advantage is weighted over.
* The **class counter triangle** (Infantry > Lancer > Marksman > Infantry, +10%)
  applies — toggle it off with ``counter_coeff=0`` if your server disagrees.
* Only **exploration skills** are active; their text is mined for arena tags
  (crowd-control / AoE / tank / heal / buff) that drive role + composition scoring.

The score is a transparent, multiplicative heuristic — positioning and counters
*modulate* a hero's raw strength rather than being added as incomparable points, so
a strong hero in the wrong seat genuinely loses value. It is calibrated to the
documented mechanics, not the game's unpublished damage formula; treat the win
probability as an estimate, especially when strengths are mixed power/derived.

Pure: dataclasses in, dataclasses out — no Redis, no device, no IO beyond loading
the one config/catalog YAML. Unit-tested in ``tests/test_optimizer.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# Canonical troop/hero classes. The wiki yaml spells marksman "Marksmen"; normalise.
CLASSES: tuple[str, ...] = ("infantry", "lancer", "marksman")

# How many engaged enemies past the first still count toward the counter term, and
# the geometric decay applied to each (the first enemy a seat faces matters most).
_COUNTER_DECAY = 0.5


def normalize_class(raw: str | None) -> str:
    """Map any spelling/casing to a canonical class id (``"Marksmen"`` → marksman)."""
    s = (raw or "").strip().lower()
    if s.startswith("marksm"):
        return "marksman"
    if s.startswith("infantr"):
        return "infantry"
    if s.startswith("lance"):
        return "lancer"
    return s


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ArenaConfig:
    """Tunable combat constants, loaded from ``games/wos/db/arena_combat.yaml``."""

    counter_coeff: float
    counter_beats: Mapping[str, str]
    slot_count: int
    front: tuple[int, ...]
    back: tuple[int, ...]
    all_target: int
    targeting: Mapping[int, tuple[int, ...]]
    w_strength: float
    w_slot_fit: float
    w_counter: float
    w_synergy: float
    rarity_weight: Mapping[str, float]
    star_step: float
    level_step: float
    skill_step: float
    power_to_rating: float
    stat_def_k: float
    stat_to_power: float
    stat_star_floor: float
    stat_level_floor: float
    stat_level_cap: float
    stat_gear_step: float
    stat_skill_step: float
    win_exponent: float
    slot_fit: Mapping[str, Mapping[str, float]]
    tag_keywords: Mapping[str, tuple[str, ...]]
    synergy: Mapping[str, float]
    hero_overrides: Mapping[str, Mapping[str, object]]

    def seat_kind(self, slot: int) -> str:
        """Seat category for slot-fit lookup: ``front`` | ``slot4`` | ``back``."""
        if slot == self.all_target:
            return "slot4"
        if slot in self.front:
            return "front"
        return "back"

    @property
    def all_slots(self) -> tuple[int, ...]:
        return tuple(range(1, self.slot_count + 1))


def _default_config_path() -> Path:
    # games/wos/core/arena/optimizer.py -> games/wos/db/arena_combat.yaml
    return Path(__file__).resolve().parents[2] / "db" / "arena_combat.yaml"


@lru_cache(maxsize=4)
def load_arena_config(path: str | None = None) -> ArenaConfig:
    """Parse the arena combat YAML into an :class:`ArenaConfig` (cached per path)."""
    p = Path(path) if path else _default_config_path()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    counter = raw.get("counter") or {}
    slots = raw.get("slots") or {}
    weights = raw.get("weights") or {}
    strength = raw.get("strength") or {}
    win = raw.get("win") or {}
    targeting_raw = slots.get("targeting") or {}
    targeting = {int(k): tuple(int(s) for s in v) for k, v in targeting_raw.items()}
    tag_keywords = {
        tag: tuple(str(w).lower() for w in words)
        for tag, words in (raw.get("tag_keywords") or {}).items()
    }
    return ArenaConfig(
        counter_coeff=float(counter.get("coeff", 0.10)),
        counter_beats={normalize_class(k): normalize_class(v) for k, v in (counter.get("beats") or {}).items()},
        slot_count=int(slots.get("count", 5)),
        front=tuple(int(s) for s in (slots.get("front") or (1, 5))),
        back=tuple(int(s) for s in (slots.get("back") or (2, 3, 4))),
        all_target=int(slots.get("all_target", 4)),
        targeting=targeting,
        w_strength=float(weights.get("strength", 1.0)),
        w_slot_fit=float(weights.get("slot_fit", 0.18)),
        w_counter=float(weights.get("counter", 1.0)),
        w_synergy=float(weights.get("synergy", 0.10)),
        rarity_weight={str(k): float(v) for k, v in (strength.get("rarity") or {}).items()},
        star_step=float(strength.get("star_step", 0.12)),
        level_step=float(strength.get("level_step", 0.010)),
        skill_step=float(strength.get("skill_step", 0.03)),
        power_to_rating=float(strength.get("power_to_rating", 0.001)),
        stat_def_k=float(strength.get("stat_def_k", 4000.0)),
        stat_to_power=float(strength.get("stat_to_power", 0.0009)),
        stat_star_floor=float(strength.get("stat_star_floor", 0.45)),
        stat_level_floor=float(strength.get("stat_level_floor", 0.40)),
        stat_level_cap=float(strength.get("stat_level_cap", 60)),
        stat_gear_step=float(strength.get("stat_gear_step", 0.012)),
        stat_skill_step=float(strength.get("stat_skill_step", 0.06)),
        win_exponent=float(win.get("exponent", 2.0)),
        slot_fit={r: {k: float(v) for k, v in cols.items()} for r, cols in (raw.get("slot_fit") or {}).items()},
        tag_keywords=tag_keywords,
        synergy={str(k): float(v) for k, v in (raw.get("synergy") or {}).items()},
        hero_overrides=raw.get("hero_overrides") or {},
    )


# --------------------------------------------------------------------------- #
# Hero / enemy inputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ArenaHero:
    """One of my heroes available to deploy. ``power`` (real in-game number) wins
    over the derived rarity rating when present."""

    id: str
    name: str
    hero_class: str                 # normalised: infantry | lancer | marksman
    rarity: str = ""
    generation: int | None = None
    star: int = 1
    level: int = 1
    skill: int = 1
    power: float | None = None
    base_attack: float | None = None   # exploration Attack ceiling (wiki stats)
    base_def: float | None = None      # exploration Defense ceiling
    base_health: float | None = None   # exploration Health ceiling
    gear_avg: float | None = None      # mean gear-piece level (from the Details screen)
    tags: tuple[str, ...] = ()      # cc | aoe | tank | heal | buff | burst
    role: str = ""                  # arena role; derived if blank


@dataclass(frozen=True, slots=True)
class EnemyHero:
    """One enemy in their lineup. Only ``slot`` + ``hero_class`` are required (class
    drives counters); ``power``/rarity sharpen the win estimate when known."""

    slot: int
    hero_class: str
    id: str = ""
    name: str = ""
    rarity: str = ""
    power: float | None = None
    star: int = 1
    level: int = 1
    skill: int = 1
    base_attack: float | None = None
    base_def: float | None = None
    base_health: float | None = None
    gear_avg: float | None = None


# --------------------------------------------------------------------------- #
# Derivations
# --------------------------------------------------------------------------- #
def derive_tags(skill_text: str, cfg: ArenaConfig) -> tuple[str, ...]:
    """Arena tags present in a hero's joined exploration-skill text (substring match)."""
    low = " ".join(skill_text.lower().split())
    out: list[str] = []
    for tag, words in cfg.tag_keywords.items():
        if any(w in low for w in words):
            out.append(tag)
    return tuple(out)


def derive_role(hero_class: str, tags: Iterable[str]) -> str:
    """Arena role from class + tags. Drives the slot-fit matrix lookup.

    **Class first** — it's the reliable signal (Infantry anchor the front, Marksmen
    are the back-line carry); the skill-text tags are noisy, so they only refine the
    flexible Lancer (and unknown) class. Curate the exceptions (a tanky lancer, a CC
    marksman) in ``hero_overrides`` rather than loosening the keyword lists.
    """
    cls = normalize_class(hero_class)
    if cls == "marksman":
        return "marksman"
    if cls == "infantry":
        return "tank"
    tagset = set(tags)                      # lancer / unknown — refine by kit
    if "heal" in tagset:
        return "healer"
    if "cc" in tagset:
        return "cc"
    if "tank" in tagset:
        return "tank"
    if "buff" in tagset:
        return "support"
    return "dps"


def _resolved_role(hero: ArenaHero) -> str:
    return hero.role or derive_role(hero.hero_class, hero.tags)


def strength_basis(hero: ArenaHero | EnemyHero) -> str:
    """Which tier resolves this unit's strength: ``power`` | ``stats`` | ``rarity``."""
    if getattr(hero, "power", None):
        return "power"
    if getattr(hero, "base_attack", None) and getattr(hero, "base_health", None):
        return "stats"
    return "rarity"


def _stat_strength(hero: ArenaHero, cfg: ArenaConfig) -> float | None:
    """Tier-2 estimate from exploration Attack/Defense/Health, discounted for the
    player's star/level and calibrated to the Power scale. ``None`` if no stats."""
    atk, hp = hero.base_attack, hero.base_health
    if not atk or not hp:
        return None
    defe = hero.base_def or 0.0
    eff_hp = hp * (1.0 + defe / cfg.stat_def_k)          # defense → effective HP
    ceiling = atk * eff_hp                                # damage output × survivability
    star_frac = min(1.0, max(0, hero.star - 1) / 5.0)     # 1★ → 0, 6★ → 1
    lvl_frac = min(1.0, max(0, hero.level - 1) / max(1.0, cfg.stat_level_cap - 1))
    star_f = cfg.stat_star_floor + (1.0 - cfg.stat_star_floor) * star_frac
    lvl_f = cfg.stat_level_floor + (1.0 - cfg.stat_level_floor) * lvl_frac
    # Gear + exploration-skill investment (from the Details screen) lift the estimate.
    gear_f = 1.0 + cfg.stat_gear_step * max(0.0, (hero.gear_avg or 1.0) - 1.0)
    skill_f = 1.0 + cfg.stat_skill_step * max(0, hero.skill - 1)
    est_power = ceiling * cfg.stat_to_power * star_f * lvl_f * gear_f * skill_f
    return est_power * cfg.power_to_rating


def hero_strength(hero: ArenaHero, cfg: ArenaConfig, *, current_generation: int | None = None) -> float:
    """Unitless combat rating, resolved power → stats → rarity (all one scale).

    1. explicit in-game ``power`` (the real signal) → ``power * power_to_rating``;
    2. exploration stats (Attack × effective-HP, star/level discounted) for heroes
       loaded from the account that carry level+star but no Power;
    3. rarity × generation decay × star/level/skill growth as the last resort.
    """
    if hero.power is not None and hero.power > 0:
        return hero.power * cfg.power_to_rating
    stat = _stat_strength(hero, cfg)
    if stat is not None:
        return stat
    base = cfg.rarity_weight.get(hero.rarity, 20.0)
    base *= _generation_factor(hero.generation, current_generation)
    base *= 1.0 + cfg.star_step * max(0, hero.star - 1)
    base *= 1.0 + cfg.level_step * max(0, hero.level - 1)
    base *= 1.0 + cfg.skill_step * max(0, hero.skill - 1)
    return base


def enemy_strength(enemy: EnemyHero, cfg: ArenaConfig, *, current_generation: int | None = None) -> float:
    """Same rating scale for an enemy seat (power → stats → rarity)."""
    proxy = ArenaHero(
        id=enemy.id, name=enemy.name, hero_class=enemy.hero_class, rarity=enemy.rarity,
        star=enemy.star, level=enemy.level, skill=enemy.skill, power=enemy.power,
        base_attack=enemy.base_attack, base_def=enemy.base_def, base_health=enemy.base_health,
        gear_avg=enemy.gear_avg,
    )
    return hero_strength(proxy, cfg, current_generation=current_generation)


def _generation_factor(gen: int | None, current: int | None) -> float:
    """Mirror of the heroes-planner generation decay (kept local to stay pure)."""
    if gen is None or current is None or gen >= current:
        return 1.0
    behind = current - gen
    if behind >= 4:
        return 0.5            # old but still a body in arena (not zeroed like investment)
    return max(0.6, 1.0 - 0.15 * behind)


def slot_fit_score(role: str, slot: int, cfg: ArenaConfig) -> float:
    """How well ``role`` fits ``slot`` ∈ [-0.4 .. 1.0] (unknown role → neutral 0)."""
    cols = cfg.slot_fit.get(role)
    if not cols:
        return 0.0
    return cols.get(cfg.seat_kind(slot), 0.0)


def counters(my_class: str, enemy_class: str, cfg: ArenaConfig) -> bool:
    """Does ``my_class`` beat ``enemy_class`` on the counter triangle?"""
    return cfg.counter_beats.get(normalize_class(my_class)) == normalize_class(enemy_class)


def counter_fraction(
    my_hero: ArenaHero, my_slot: int, enemy_by_slot: Mapping[int, EnemyHero], cfg: ArenaConfig
) -> float:
    """Counter advantage ∈ [0 .. counter_coeff] this seat earns, decay-weighted over
    the enemies it engages (per the targeting order). 0 when counters are disabled or
    no enemy is supplied."""
    if cfg.counter_coeff <= 0 or not enemy_by_slot:
        return 0.0
    order = [s for s in cfg.targeting.get(my_slot, ()) if s in enemy_by_slot]
    if not order:
        return 0.0
    num = 0.0
    den = 0.0
    w = 1.0
    for eslot in order:
        den += w
        if counters(my_hero.hero_class, enemy_by_slot[eslot].hero_class, cfg):
            num += w
        w *= _COUNTER_DECAY
    return cfg.counter_coeff * (num / den) if den else 0.0


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SlotAssignment:
    slot: int
    seat: str                       # front | slot4 | back
    hero_id: str
    hero_name: str
    hero_class: str
    role: str
    strength: float
    slot_fit: float
    counter: float                  # counter fraction earned (0..coeff)
    effective: float                # strength after positional multipliers
    engaged: tuple[int, ...]        # enemy slots this seat engages
    note: str = ""


@dataclass(frozen=True, slots=True)
class Placement:
    slots: tuple[SlotAssignment, ...]
    score: float
    strength_total: float           # positionally-adjusted own strength
    win_prob: float | None          # vs the enemy lineup (None if no enemy given)
    synergy_units: float
    power_ratio: float | None = None  # my effective power / enemy power (None if no enemy)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LineupPlan:
    best: Placement | None
    alternatives: tuple[Placement, ...]
    reason: str                     # selected | no_heroes | ...
    enemy_strength: float
    counter_enabled: bool
    confidence: str = "low"          # high (all Power) | medium (stats) | low (rarity/mixed)
    bench: tuple[str, ...] = ()      # hero ids not placed
    notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _synergy_units(assignment: Mapping[int, ArenaHero], cfg: ArenaConfig) -> float:
    """Board-level composition bonuses/penalties (summed, applied once)."""
    syn = cfg.synergy
    units = 0.0
    roles = {slot: _resolved_role(h) for slot, h in assignment.items()}
    classes = [h.hero_class for h in assignment.values()]
    tags = {t for h in assignment.values() for t in h.tags}

    front_roles = [roles[s] for s in cfg.front if s in roles]
    if any(r in ("tank", "cc") for r in front_roles):
        units += syn.get("tank_in_front", 0.0)
    if cfg.all_target in roles and roles[cfg.all_target] in ("marksman", "dps"):
        units += syn.get("marksman_in_slot4", 0.0)
    if "cc" in tags or any(r == "cc" for r in roles.values()):
        units += syn.get("has_cc", 0.0)
    if "heal" in tags or any(r == "healer" for r in roles.values()):
        units += syn.get("has_healer", 0.0)

    pen = syn.get("duplicate_class_penalty", 0.0)
    if pen:
        for cls in CLASSES:
            extra = classes.count(cls) - 2
            if extra > 0:
                units += pen * extra
    return units


def score_placement(
    assignment: Mapping[int, ArenaHero],
    enemy_by_slot: Mapping[int, EnemyHero],
    cfg: ArenaConfig,
    *,
    current_generation: int | None = None,
    enemy_total: float | None = None,
) -> Placement:
    """Score one fully-formed assignment (slot → hero) into a :class:`Placement`."""
    rows: list[SlotAssignment] = []
    raw_total = 0.0
    for slot in sorted(assignment):
        hero = assignment[slot]
        role = _resolved_role(hero)
        strength = hero_strength(hero, cfg, current_generation=current_generation)
        fit = slot_fit_score(role, slot, cfg)
        cfrac = counter_fraction(hero, slot, enemy_by_slot, cfg)
        effective = strength * (1.0 + cfg.w_slot_fit * fit + cfg.w_counter * cfrac)
        raw_total += effective
        rows.append(SlotAssignment(
            slot=slot, seat=cfg.seat_kind(slot), hero_id=hero.id, hero_name=hero.name,
            hero_class=hero.hero_class, role=role, strength=round(strength, 2),
            slot_fit=round(fit, 3), counter=round(cfrac, 4), effective=round(effective, 2),
            engaged=tuple(s for s in cfg.targeting.get(slot, ()) if s in enemy_by_slot),
            note=_seat_note(role, slot, cfg),
        ))

    units = _synergy_units(assignment, cfg)
    my_eff = raw_total * (1.0 + cfg.w_synergy * units)   # synergy-adjusted effective power
    win = _win_prob(my_eff, enemy_total, cfg.win_exponent) if enemy_total else None
    ratio = round(my_eff / enemy_total, 3) if enemy_total else None
    return Placement(
        slots=tuple(rows), score=round(my_eff, 2), strength_total=round(raw_total, 2),
        win_prob=win, synergy_units=round(units, 3), power_ratio=ratio,
        warnings=_warnings(assignment, cfg),
    )


def _seat_note(role: str, slot: int, cfg: ArenaConfig) -> str:
    seat = cfg.seat_kind(slot)
    if seat == "slot4":
        return "all-target seat — carry" if role in ("marksman", "dps") else "carry seat wasted on a non-DPS"
    if seat == "front":
        if role in ("tank", "cc"):
            return "front anchor"
        if role == "marksman":
            return "marksman exposed in front"
        return "exposed front seat"
    return "back line"


def _warnings(assignment: Mapping[int, ArenaHero], cfg: ArenaConfig) -> tuple[str, ...]:
    out: list[str] = []
    roles = {slot: _resolved_role(h) for slot, h in assignment.items()}
    front_roles = [roles[s] for s in cfg.front if s in roles]
    if front_roles and not any(r in ("tank", "cc") for r in front_roles):
        out.append("no tank or CC in a front seat — your line folds early")
    out.extend(
        f"marksman in front slot {s} — very exposed"
        for s in cfg.front if roles.get(s) == "marksman"
    )
    if cfg.all_target in roles and roles[cfg.all_target] not in ("marksman", "dps"):
        out.append(f"slot {cfg.all_target} (hits all 5) is not a damage carry")
    return tuple(out)


def _win_prob(my_total: float, enemy_total: float | None, exponent: float) -> float | None:
    """Lanchester power-ratio win probability: ``me^n / (me^n + enemy^n)``.

    No published damage formula exists, so the arena outcome is modelled as a
    power-ratio contest. ``my_total`` already folds in counters + positioning, so a
    seat/counter edge tilts the number even at equal raw power. n=2 is the square law.
    """
    if not enemy_total or enemy_total <= 0 or my_total <= 0:
        return None
    n = max(0.5, exponent)
    me, en = my_total**n, enemy_total**n
    return round(me / (me + en), 3)


# --------------------------------------------------------------------------- #
# Optimisation
# --------------------------------------------------------------------------- #
def optimize_lineup(
    my_heroes: Sequence[ArenaHero],
    enemy_lineup: Sequence[EnemyHero] = (),
    *,
    cfg: ArenaConfig | None = None,
    locked: Mapping[int, str] | None = None,
    current_generation: int | None = None,
    top_k: int = 3,
    pool_cap: int = 12,
) -> LineupPlan:
    """Best arrangement of ``my_heroes`` into the 5 seats against ``enemy_lineup``.

    Strongest heroes are pre-filtered to ``pool_cap`` (positional multipliers are
    small, so a hero far down the strength list can't win a seat), then every
    arrangement of the unlocked seats is scored and the top ``top_k`` kept.
    ``locked`` pins heroes (by id) to seats — the manual drag overrides from the UI.
    """
    cfg = cfg or load_arena_config()
    locked = {int(k): v for k, v in (locked or {}).items()}
    if not my_heroes:
        return LineupPlan(None, (), "no_heroes", 0.0, cfg.counter_coeff > 0)

    by_id = {h.id: h for h in my_heroes}
    enemy_by_slot = {e.slot: e for e in enemy_lineup}
    enemy_total = sum(
        enemy_strength(e, cfg, current_generation=current_generation) for e in enemy_lineup
    ) or None

    # Pinned heroes occupy their seats; everyone else competes for the rest.
    pinned: dict[int, ArenaHero] = {}
    for slot, hid in locked.items():
        if hid in by_id and 1 <= slot <= cfg.slot_count:
            pinned[slot] = by_id[hid]
    pinned_ids = {h.id for h in pinned.values()}
    open_slots = [s for s in cfg.all_slots if s not in pinned]

    pool = [h for h in my_heroes if h.id not in pinned_ids]
    pool.sort(key=lambda h: hero_strength(h, cfg, current_generation=current_generation), reverse=True)
    pool = pool[:pool_cap]

    take = min(len(open_slots), len(pool))
    fill_slots = open_slots[:take] if take else []

    scored: list[Placement] = []
    seen: set[tuple[str, ...]] = set()
    if not fill_slots:
        scored.append(score_placement(
            pinned, enemy_by_slot, cfg,
            current_generation=current_generation, enemy_total=enemy_total,
        ))
    else:
        for combo in permutations(pool, take):
            assignment = {**pinned, **dict(zip(fill_slots, combo, strict=True))}
            key = tuple(assignment[s].id for s in sorted(assignment))
            if key in seen:
                continue
            seen.add(key)
            scored.append(score_placement(
                assignment, enemy_by_slot, cfg,
                current_generation=current_generation, enemy_total=enemy_total,
            ))

    scored.sort(key=lambda p: p.score, reverse=True)
    best = scored[0] if scored else None
    placed_ids = {row.hero_id for row in best.slots} if best else set()
    bench = tuple(h.id for h in my_heroes if h.id not in placed_ids)

    placed = [by_id[i] for i in placed_ids if i in by_id]
    bases = [strength_basis(h) for h in placed] + [strength_basis(e) for e in enemy_lineup]
    if bases and all(b == "power" for b in bases):
        confidence = "high"
    elif bases and all(b in ("power", "stats") for b in bases):
        confidence = "medium"
    else:
        confidence = "low"

    notes: list[str] = []
    if enemy_total is None:
        notes.append("no enemy lineup given — arrangement optimises seat-fit and synergy only")
    elif confidence != "high":
        notes.append(
            "win probability is a model estimate — enter each hero's in-game Power on both "
            "sides for a high-confidence number (the account stores level+star, not Power)"
        )

    return LineupPlan(
        best=best,
        alternatives=tuple(scored[1:top_k]) if best else (),
        reason="selected" if best else "no_heroes",
        enemy_strength=round(enemy_total or 0.0, 2),
        counter_enabled=cfg.counter_coeff > 0 and bool(enemy_by_slot),
        confidence=confidence,
        bench=bench,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Catalog: build ArenaHero defaults from the hero wiki db (class/rarity/tags)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=2)
def load_arena_catalog(directory: str | None = None) -> dict[str, ArenaHero]:
    """``id → ArenaHero`` template (class, rarity, derived tags/role) from the hero
    wiki yaml. The API overlays the operator's owned star/level/skill/power."""
    cfg = load_arena_config()
    if directory is not None:
        d = Path(directory)
    else:
        from config.heroes import heroes_db_dir

        d = heroes_db_dir()
    out: dict[str, ArenaHero] = {}
    for path in sorted(Path(d).glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        hid = str(raw["id"])
        skill_text = " ".join(
            str(s.get("description", "")) for s in (raw.get("skills") or []) if isinstance(s, dict)
        )
        override = cfg.hero_overrides.get(hid) or {}
        tags = tuple(override.get("tags") or derive_tags(skill_text, cfg))
        cls = normalize_class(raw.get("class"))
        expl = (raw.get("stats") or {}).get("exploration") or {}
        out[hid] = ArenaHero(
            id=hid,
            name=str(raw.get("name") or hid),
            hero_class=cls,
            rarity=str(raw.get("rarity") or ""),
            base_attack=_to_float(expl.get("attack")),
            base_def=_to_float(expl.get("def")),
            base_health=_to_float(expl.get("health")),
            tags=tags,
            role=str(override.get("arena_role") or derive_role(cls, tags)),
        )
    return out


def _to_float(value: object) -> float | None:
    """Parse a wiki stat (often a comma/percent string) to float, or ``None``."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
