"""The shared even-leveling ladder core (used by charms / chief-gear / hero-gear)."""
from __future__ import annotations

from games.wos.core.ladder import even_leveling_value
from games.wos.core.roles import get_role


def test_even_leveling_unnormalised_is_lagging_first():
    # recency = max(1, max - to + 1): L1 of a 16-ladder scores 16×, the top step 1×.
    assert even_leveling_value(1, max_level=16, composition=0.34, base=100.0) == 100.0 * 0.34 * 16
    assert even_leveling_value(16, max_level=16, composition=0.34, base=100.0) == 100.0 * 0.34 * 1


def test_even_leveling_normalised_is_scale_independent():
    # recency = (max - to + 1)/max → L1 of any ladder ≈ 1.0; comparable across scales.
    assert even_leveling_value(1, max_level=100, composition=0.34, base=100.0, normalize=True) \
        == 100.0 * 0.34 * 1.0
    assert even_leveling_value(50, max_level=100, composition=0.34, base=100.0, normalize=True) \
        == 100.0 * 0.34 * (51 / 100)


def test_role_tilts_the_value():
    fighter, farm = get_role("fighter"), get_role("farm")
    f = even_leveling_value(1, max_level=16, composition=0.34, base=100.0, role=fighter,
                            role_category="battle")
    g = even_leveling_value(1, max_level=16, composition=0.34, base=100.0, role=farm,
                            role_category="battle")
    assert f > g                                   # battle tilt lifts the fighter


def test_domain_wrappers_match_the_core():
    # The thin policy wrappers must reproduce the core exactly.
    from games.wos.core.charms.planner import charm_value
    from games.wos.core.gear.planner import gear_value
    from games.wos.core.hero_gear.planner import TRACK_WEIGHT, hero_gear_value

    assert charm_value("infantry", 3, max_level=16) \
        == even_leveling_value(3, max_level=16, composition=0.34, base=100.0)
    assert gear_value("lancer", 5, max_level=42) \
        == even_leveling_value(5, max_level=42, composition=0.33, base=100.0)
    assert hero_gear_value("infantry", "mastery", 4, max_level=20) \
        == TRACK_WEIGHT["mastery"] * even_leveling_value(
            4, max_level=20, composition=0.34, base=100.0, normalize=True)
