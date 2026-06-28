"""Arena lineup optimizer: class counters, slot-fit, strength, and placement search."""
from __future__ import annotations

import pytest
from games.wos.core.arena.optimizer import (
    ArenaHero,
    EnemyHero,
    _win_prob,
    counter_fraction,
    counters,
    derive_role,
    derive_tags,
    hero_strength,
    load_arena_catalog,
    load_arena_config,
    normalize_class,
    optimize_lineup,
    slot_fit_score,
    strength_basis,
)

CFG = load_arena_config()


def _hero(hid: str, cls: str, *, role: str = "", power: float | None = None,
          star: int = 1, level: int = 1, skill: int = 1, rarity: str = "Legendary",
          tags: tuple[str, ...] = ()) -> ArenaHero:
    return ArenaHero(id=hid, name=hid.title(), hero_class=normalize_class(cls), rarity=rarity,
                     role=role, power=power, star=star, level=level, skill=skill, tags=tags)


# --- config -----------------------------------------------------------------
def test_config_loads_documented_layout():
    assert CFG.slot_count == 5
    assert set(CFG.front) == {1, 5}
    assert CFG.all_target == 4
    assert CFG.counter_coeff == pytest.approx(0.10)
    # slot 1's documented targeting order
    assert CFG.targeting[1] == (2, 5, 4, 3)


def test_seat_kind():
    assert CFG.seat_kind(1) == "front"
    assert CFG.seat_kind(5) == "front"
    assert CFG.seat_kind(4) == "slot4"      # all-target seat wins over "back"
    assert CFG.seat_kind(2) == "back"


# --- class normalisation + counter triangle ---------------------------------
@pytest.mark.parametrize(("raw", "expected"), [
    ("Marksmen", "marksman"), ("Marksman", "marksman"),
    ("Infantry", "infantry"), ("Lancer", "lancer"), ("  lancer ", "lancer"),
])
def test_normalize_class(raw, expected):
    assert normalize_class(raw) == expected


def test_counter_triangle():
    assert counters("infantry", "lancer", CFG)
    assert counters("lancer", "marksman", CFG)
    assert counters("marksman", "infantry", CFG)
    # reverse never counters
    assert not counters("lancer", "infantry", CFG)
    assert not counters("infantry", "marksman", CFG)
    assert not counters("infantry", "infantry", CFG)


# --- tag + role derivation --------------------------------------------------
def test_derive_tags_from_skill_text():
    # Ahmose-style: shield/damage-reduction (tank) + damage buff (buff) + control immunity.
    text = ("entering an invulnerable state, immune to control effects and reducing "
            "damage taken by 30% for nearby friendly troops; increasing their damage dealt")
    tags = set(derive_tags(text, CFG))
    assert "tank" in tags
    assert "buff" in tags


def test_derive_role_precedence():
    # Class is the primary signal; tags only refine the flexible Lancer class.
    assert derive_role("infantry", []) == "tank"          # infantry anchor the front
    assert derive_role("marksman", []) == "marksman"      # marksmen are the carry
    assert derive_role("marksman", ["tank"]) == "marksman"  # class wins over noisy tags
    assert derive_role("lancer", ["heal"]) == "healer"
    assert derive_role("lancer", ["cc"]) == "cc"
    assert derive_role("lancer", []) == "dps"


# --- strength ----------------------------------------------------------------
def test_explicit_power_beats_derived():
    weak_legendary = _hero("a", "infantry", rarity="Legendary")
    strong_by_power = _hero("b", "rare", rarity="Rare", power=500_000)
    assert hero_strength(strong_by_power, CFG) > hero_strength(weak_legendary, CFG)


def test_strength_grows_with_investment():
    base = _hero("a", "infantry", star=1, level=1, skill=1)
    invested = _hero("a", "infantry", star=5, level=40, skill=8)
    assert hero_strength(invested, CFG) > hero_strength(base, CFG)


# --- slot fit ----------------------------------------------------------------
def test_marksman_best_in_slot4_worst_in_front():
    assert slot_fit_score("marksman", 4, CFG) > slot_fit_score("marksman", 2, CFG)
    assert slot_fit_score("marksman", 1, CFG) < 0          # exposed in front
    assert slot_fit_score("tank", 1, CFG) > slot_fit_score("tank", 4, CFG)


# --- counter fraction --------------------------------------------------------
def test_counter_fraction_rewards_countering_engaged_enemy():
    enemy = {2: EnemyHero(slot=2, hero_class="lancer")}      # slot 1 engages slot 2 first
    inf = _hero("inf", "infantry", role="dps")
    mk = _hero("mk", "marksman", role="dps")
    assert counter_fraction(inf, 1, enemy, CFG) > 0          # infantry beats lancer
    assert counter_fraction(mk, 1, enemy, CFG) == 0          # marksman doesn't


def test_counter_fraction_capped_and_toggleable():
    enemy = {s: EnemyHero(slot=s, hero_class="lancer") for s in range(1, 6)}
    inf = _hero("inf", "infantry", role="dps")
    frac = counter_fraction(inf, 4, enemy, CFG)              # slot 4 engages everyone
    assert frac == pytest.approx(CFG.counter_coeff)          # all engaged are countered → capped
    off = load_arena_config()  # cached same obj; build a disabled variant manually
    from dataclasses import replace
    disabled = replace(off, counter_coeff=0.0)
    assert counter_fraction(inf, 4, enemy, disabled) == 0.0


# --- full optimisation -------------------------------------------------------
def _roster_equal_power() -> list[ArenaHero]:
    p = 200_000
    return [
        _hero("tank", "infantry", role="tank", power=p),
        _hero("cc", "infantry", role="cc", power=p, tags=("cc",)),
        _hero("mk", "marksman", role="marksman", power=p),
        _hero("dps1", "lancer", role="dps", power=p),
        _hero("dps2", "lancer", role="dps", power=p),
    ]


def test_optimizer_seats_roles_sensibly():
    plan = optimize_lineup(_roster_equal_power(), [], cfg=CFG)
    assert plan.best is not None
    seat = {row.slot: row for row in plan.best.slots}
    assert seat[4].hero_id == "mk"                           # carry in the all-target seat
    assert {seat[1].role, seat[5].role} <= {"tank", "cc"}    # front anchored by tank/CC
    assert seat[2].role == "dps" and seat[3].role == "dps"   # dps in the back pair


def test_optimizer_benches_the_weakest():
    roster = [*_roster_equal_power(), _hero("scrub", "lancer", role="dps", power=1_000)]
    plan = optimize_lineup(roster, [], cfg=CFG)
    assert plan.best is not None
    assert "scrub" in plan.bench
    assert len(plan.best.slots) == 5


def test_locked_slot_is_honoured():
    plan = optimize_lineup(_roster_equal_power(), [], cfg=CFG, locked={1: "mk"})
    assert plan.best is not None
    seat = {row.slot: row for row in plan.best.slots}
    assert seat[1].hero_id == "mk"                           # pinned despite poor front-fit


def test_no_enemy_gives_no_winprob_but_still_places():
    plan = optimize_lineup(_roster_equal_power(), [], cfg=CFG)
    assert plan.best is not None
    assert plan.best.win_prob is None
    assert any("no enemy lineup" in n for n in plan.notes)


def _enemy_lineup(power: float) -> list[EnemyHero]:
    classes = ["infantry", "lancer", "marksman", "marksman", "infantry"]
    return [EnemyHero(slot=i + 1, hero_class=c, power=power) for i, c in enumerate(classes)]


def test_winprob_monotonic_in_my_strength():
    enemy = _enemy_lineup(200_000)
    weak = optimize_lineup(
        [_hero(f"h{i}", "lancer", role="dps", power=120_000) for i in range(5)], enemy, cfg=CFG)
    strong = optimize_lineup(
        [_hero(f"h{i}", "lancer", role="dps", power=400_000) for i in range(5)], enemy, cfg=CFG)
    assert weak.best.win_prob is not None and strong.best.win_prob is not None
    assert strong.best.win_prob > weak.best.win_prob
    assert 0.0 < weak.best.win_prob < strong.best.win_prob < 1.0


def test_warns_on_marksman_in_front():
    roster = _roster_equal_power()
    plan = optimize_lineup(roster, [], cfg=CFG, locked={1: "mk"})
    assert any("marksman in front" in w for w in plan.best.warnings)


def test_empty_roster():
    plan = optimize_lineup([], [], cfg=CFG)
    assert plan.best is None
    assert plan.reason == "no_heroes"


# --- catalog loader (reads the real hero wiki db) ---------------------------
def test_catalog_builds_arena_heroes():
    cat = load_arena_catalog()
    assert cat, "expected the hero wiki db to yield arena heroes"
    # Ahmose is a Legendary Infantry with a shield/damage-reduction kit → tanky.
    ahmose = cat.get("ahmose")
    assert ahmose is not None
    assert ahmose.hero_class == "infantry"
    assert ahmose.role in {"tank", "cc", "support", "dps"}
    # base combat stats are now parsed from stats.exploration
    assert ahmose.base_attack and ahmose.base_health


# --- stat-based strength tier (account heroes: level+star, no power) ---------
def test_stat_tier_used_when_no_power():
    strong = ArenaHero(id="s", name="S", hero_class="infantry", star=5, level=50,
                       base_attack=3000, base_def=4000, base_health=60000)
    weak = ArenaHero(id="w", name="W", hero_class="infantry", star=1, level=1,
                     base_attack=1200, base_def=1500, base_health=12000)
    assert strength_basis(strong) == "stats"
    assert strength_basis(weak) == "stats"
    assert hero_strength(strong, CFG) > hero_strength(weak, CFG)  # more stars/level + bigger stats


def test_basis_precedence():
    assert strength_basis(_hero("p", "infantry", power=1)) == "power"
    assert strength_basis(ArenaHero(id="s", name="S", hero_class="infantry",
                                    base_attack=1000, base_health=10000)) == "stats"
    assert strength_basis(_hero("r", "infantry", rarity="Legendary")) == "rarity"


# --- Lanchester win model ----------------------------------------------------
def test_lanchester_win_curve():
    assert _win_prob(100.0, 100.0, 2.0) == 0.5                            # equal → coinflip
    assert _win_prob(150.0, 100.0, 2.0) > 0.6                            # 1.5× edge
    assert _win_prob(200.0, 100.0, 2.0) == pytest.approx(0.8, abs=0.01)  # 2× → ~0.8 (square law)
    assert _win_prob(100.0, 200.0, 2.0) == pytest.approx(0.2, abs=0.01)
    assert _win_prob(100.0, None, 2.0) is None
    assert _win_prob(100.0, 0.0, 2.0) is None


def test_positioning_edge_lifts_winprob_at_equal_power():
    my = [
        _hero("inf1", "infantry", role="tank", power=200_000),
        _hero("inf2", "infantry", role="tank", power=200_000),
        _hero("mk", "marksman", role="marksman", power=200_000),
        _hero("l1", "lancer", role="dps", power=200_000),
        _hero("l2", "lancer", role="dps", power=200_000),
    ]
    enemy = [EnemyHero(slot=i + 1, hero_class="lancer", power=200_000) for i in range(5)]
    plan = optimize_lineup(my, enemy, cfg=CFG)
    # equal raw power, but counters (infantry>lancer) + seating raise effective power
    assert plan.best.power_ratio > 1.0
    assert plan.best.win_prob > 0.5


# --- confidence reporting ----------------------------------------------------
def test_confidence_high_when_all_power():
    my = [_hero(f"h{i}", "lancer", role="dps", power=300_000) for i in range(5)]
    enemy = [EnemyHero(slot=i + 1, hero_class="lancer", power=280_000) for i in range(5)]
    plan = optimize_lineup(my, enemy, cfg=CFG)
    assert plan.confidence == "high"
    assert plan.best.power_ratio is not None and plan.best.win_prob is not None


def test_confidence_low_when_rarity_only():
    my = [_hero(f"h{i}", "lancer", role="dps", rarity="Legendary") for i in range(5)]
    enemy = [EnemyHero(slot=i + 1, hero_class="lancer") for i in range(5)]
    plan = optimize_lineup(my, enemy, cfg=CFG)
    assert plan.confidence == "low"
    assert any("model estimate" in n for n in plan.notes)


def test_gear_and_skill_raise_stat_strength():
    # same base stats / star / level — gear + maxed skills (read off the Details screen)
    # lift the no-Power stat estimate.
    base = dict(id="h", name="H", hero_class="infantry", star=4, level=60,
                base_attack=3000, base_def=4000, base_health=60000)
    plain = ArenaHero(**base, skill=1)
    geared = ArenaHero(**base, skill=5, gear_avg=16.0)
    assert hero_strength(geared, CFG) > hero_strength(plain, CFG)
    # gear alone and skill alone each help
    assert hero_strength(ArenaHero(**base, gear_avg=16.0), CFG) > hero_strength(plain, CFG)
    assert hero_strength(ArenaHero(**base, skill=5), CFG) > hero_strength(plain, CFG)
