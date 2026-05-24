"""Solver / scorer priors, baked into Python so Nuitka can absorb them.

Previously lived as four separate YAML files in this directory; merged here so
the compiled ``config.so`` carries the values directly. Each constant mirrors
the shape of the YAML it replaces:

    DEFAULTS      ← defaults.yaml      (sunkness / scarcity / threshold / solver)
    PROFILES      ← profiles.yaml      (active + ``profiles`` dict)
    HERO_META     ← hero_meta.yaml     (``defaults`` + per-hero ``overrides``)
    COST_TABLES   ← cost_tables.yaml   (hero_xp_v1, skill_manual_costs_v1, …)

Source priors: ``deep-research-report.md`` ("Scoring model", "OR-Tools CP-SAT
model and candidate generation", "Data model and YAML DSL", "cost_table.yaml
format"). When updating, treat this module as data — keep keys / value shapes
identical to the original YAML so the optimizer doesn't need to special-case.
"""
from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    # Sunkness: how irreversible each kind of investment is (0..1, higher =
    # harder to undo). Drives ``replacement_penalty`` in the score formula.
    "sunkness": {
        "level_up_pre_drill": 0.35,
        "level_up_post_drill": 0.15,
        "skill_up": 0.60,
        "star_tier_up_specific_shards": 0.80,
        "star_tier_up_general_shards": 1.00,
        "gear_enhance": 0.55,
        "exclusive_widgets": 1.00,
        "gems_during_wheel_reserve": 0.95,
    },
    # Scarcity weights per resource (relative; higher = more valuable to
    # preserve). Drives ``resource_rarity_penalty`` so the scorer avoids
    # gem/widget leaks.
    "scarcity": {
        "hero_xp": 0.30,
        "rare_shard": 0.40,
        "epic_shard": 0.60,
        "mythic_general_shard": 1.00,
        "mythic_specific_shard": 0.70,
        "epic_expedition_manual": 0.70,
        "epic_exploration_manual": 0.60,
        "enhancement_xp": 0.40,
        "essence_stones": 0.70,
        "widget": 1.00,
        "gems": 0.95,
    },
    # Discrete thresholds the scorer treats as step functions rather than
    # linear gain.
    "threshold_bonuses": {
        "bear_join_skill_5": 2500,  # first expedition skill of a joiner reaching L5
        "star_5_skill_cap": 800,    # unlocks a new skill level cap
    },
    # Time budgets for CP-SAT (handed to scorer, surfaced for tuning).
    # NOTE: ``num_search_workers > 1`` hangs CP-SAT on macOS ortools 9.x in
    # this venv. Stay single-worker until we verify a multi-worker config
    # that ships clean; bump on Linux/CI when we get there.
    "solver": {
        "online": {
            "max_time_in_seconds": 0.30,
            "num_search_workers": 1,
        },
        "batch": {
            "max_time_in_seconds": 5.0,
            "num_search_workers": 1,
        },
        "random_seed": 42,
    },
}


PROFILES: dict[str, Any] = {
    "active": "conservative_long_term_f2p",
    "profiles": {
        "conservative_long_term_f2p": {
            "description": (
                "Default F2P opening. Saves gems for the next Lucky Wheel hero, "
                "blocks mythic general shards entirely, allows epic generals only "
                "on the core trio + Jessie."
            ),
            "objective_weights": {
                "expedition": 35,
                "exploration": 30,
                "arena": 20,
                "bear_join": 15,
            },
            "wheel_policy": "reserve_for_next_gen",
            "general_shard_policy": {
                "mythic": {"mode": "deny_by_default", "allow_heroes": []},
                "epic": {
                    "mode": "allow_core_only",
                    "allow_heroes": ["sergey", "bahiti", "jessie"],
                },
            },
        },
        "aggressive_opening_f2p": {
            "description": (
                "Faster first-month progress on Molly. Spends mythic general "
                "shards on Molly up to Star 3, allows more epic general usage. "
                "Wheel reserve relaxes when current Gen blocks progress."
            ),
            "objective_weights": {
                "expedition": 30,
                "exploration": 35,
                "arena": 20,
                "bear_join": 15,
            },
            "wheel_policy": "current_gen_if_blocked_progress",
            "general_shard_policy": {
                "mythic": {
                    "mode": "allow_threshold_only",
                    "allow_heroes": {"molly": {"max_star": 3}},
                },
                "epic": {
                    "mode": "allow_core_only",
                    "allow_heroes": ["sergey", "bahiti", "jessie", "gina"],
                },
            },
        },
        "bear_alliance_support": {
            "description": (
                "Alliance KPI is Bear damage. Joiner skills and manuals come "
                "first, gems and widgets are guarded harder than in the "
                "conservative profile."
            ),
            "objective_weights": {
                "expedition": 25,
                "exploration": 20,
                "arena": 15,
                "bear_join": 40,
            },
            "wheel_policy": "reserve_for_next_gen",
            "general_shard_policy": {
                "mythic": {"mode": "deny_by_default", "allow_heroes": []},
                "epic": {
                    "mode": "allow_core_only",
                    "allow_heroes": ["jessie", "jasser", "seo_yoon"],
                },
            },
        },
    },
}


HERO_META: dict[str, Any] = {
    # Heroes not listed under ``overrides`` inherit ``defaults`` verbatim.
    "defaults": {
        "role_tags": ["generic"],
        "mode_weights": {
            "expedition": 40,
            "exploration": 30,
            "arena": 20,
            "bear_join": 10,
        },
        "skill_priority": {
            "expedition": [1],
            "exploration": [1, 2],
            "arena": [1],
        },
        "general_shard_policy": "deny_by_default",
        "manual_level_cap_pre_drill": 30,
        "manual_level_cap_post_drill": 60,
        # Risk that this hero is dropped from active lineup within N days from
        # account start. Multiplied with sunkness to compute replacement_penalty.
        "replacement_risk_curve": {
            30: 0.10,
            60: 0.30,
            90: 0.50,
            120: 0.70,
        },
    },
    "overrides": {
        # --- Core trio (F2P expedition core) -----------------------------
        "molly": {
            "role_tags": ["core", "exploration_carry", "arena_carry"],
            "mode_weights": {
                "expedition": 80, "exploration": 90, "arena": 85, "bear_join": 30,
            },
            "general_shard_policy": "allow_threshold_only",
        },
        "sergey": {
            "role_tags": ["core", "tank", "expedition_frontline"],
            "mode_weights": {
                "expedition": 75, "exploration": 30, "arena": 20, "bear_join": 40,
            },
            "skill_priority": {"expedition": [1], "exploration": [2]},
            "general_shard_policy": "allow_core_only",
        },
        "bahiti": {
            "role_tags": ["core", "marksman", "expedition_dps"],
            "mode_weights": {
                "expedition": 75, "exploration": 35, "arena": 30, "bear_join": 60,
            },
            "general_shard_policy": "allow_core_only",
        },
        # --- Dual-role / Bear-relevant -----------------------------------
        "jessie": {
            "role_tags": ["dual_role_support", "bear_joiner"],
            "mode_weights": {
                "expedition": 40, "exploration": 60, "arena": 25, "bear_join": 95,
            },
            # bear threshold first, exploration follows
            "skill_priority": {"expedition": [1], "exploration": [1, 2]},
            "general_shard_policy": "allow_core_only",
        },
        "gina": {
            "role_tags": ["exploration_support", "beast_utility"],
            "mode_weights": {
                "expedition": 25, "exploration": 70, "arena": 20, "bear_join": 50,
            },
        },
        "jasser": {
            "role_tags": ["joiner_only", "bear_specialist"],
            "mode_weights": {
                "expedition": 20, "exploration": 10, "arena": 10, "bear_join": 90,
            },
            "skill_priority": {"expedition": [1]},
            "manual_level_cap_pre_drill": 10,
            "manual_level_cap_post_drill": 0,
        },
        "seo_yoon": {
            "role_tags": ["joiner_only", "bear_specialist"],
            "mode_weights": {
                "expedition": 20, "exploration": 15, "arena": 15, "bear_join": 80,
            },
            "skill_priority": {"expedition": [1]},
            "manual_level_cap_pre_drill": 10,
            "manual_level_cap_post_drill": 0,
        },
        # --- Wheel path ---------------------------------------------------
        "zinman": {
            "role_tags": ["wheel_path", "gen1_wheel"],
            "mode_weights": {
                "expedition": 40, "exploration": 35, "arena": 30, "bear_join": 30,
            },
        },
        # --- Gen2 replacement wave ---------------------------------------
        "flint": {
            "role_tags": ["gen2_replacement_carry"],
            "mode_weights": {
                "expedition": 70, "exploration": 65, "arena": 55, "bear_join": 40,
            },
            "replacement_risk_curve": {30: 0.05, 60: 0.15, 90: 0.30, 120: 0.50},
        },
        "alonso": {
            "role_tags": ["gen2_replacement"],
            "mode_weights": {
                "expedition": 60, "exploration": 55, "arena": 50, "bear_join": 35,
            },
            "replacement_risk_curve": {30: 0.05, 60: 0.15, 90: 0.30, 120: 0.50},
        },
    },
}


COST_TABLES: dict[str, Any] = {
    "hero_xp_v1": {
        "unit": "hero_xp",
        "max_level": 80,
        # cumulative XP to reach the listed level from the previous one
        "per_level": {
            2: 480, 3: 690, 4: 920, 5: 1200,
            10: 3100, 20: 13000, 30: 24000, 40: 58000,
            50: 130000, 60: 300000, 70: 770000, 80: 2400000,
        },
    },
    # Furnace gates: ``level_up`` for a hero is denied above the cap matching
    # the player's furnace level. Read by candidate generator.
    "hero_level_cap_by_furnace_v1": {
        20: 4, 21: 10, 23: 11, 26: 12, 29: 13, 32: 14, 35: 15,
        38: 16, 41: 17, 44: 18, 47: 19, 50: 20, 55: 21, 60: 22,
        65: 23, 70: 24, 75: 25, 80: 26,
    },
    # Skill manual costs to advance one skill from ``from_level`` → ``from_level + 1``.
    # Cost lookup: ``[rarity][track][from_level]``. ``from_level=0`` means
    # "unlock the skill" (only legal once unlocked-via-star).
    "skill_manual_costs_v1": {
        "unit": "skill_manual",
        "rare": {
            "expedition":  {0: 1, 1: 1, 2: 2, 3: 3, 4: 5},
            "exploration": {0: 1, 1: 1, 2: 2, 3: 3, 4: 5},
        },
        "epic": {
            "expedition":  {0: 1, 1: 2, 2: 4, 3: 8, 4: 16},
            "exploration": {0: 1, 1: 2, 2: 4, 3: 8, 4: 16},
        },
        "mythic": {
            "expedition":  {0: 2, 1: 4, 2: 8, 3: 16, 4: 32},
            "exploration": {0: 2, 1: 4, 2: 8, 3: 16, 4: 32},
        },
    },
    # Star-level cap on skill level: at ★N the highest legal skill level is
    # ``[N]``. Heroes can keep accumulating manuals past the current cap, but
    # the optimizer won't generate a candidate above it.
    "skill_level_cap_by_star_v1": {
        0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5,
    },
}


# Mapping that ``balance_api.read_balance_file`` exposes to the UI. Keep in
# sync with whatever UI labels the Balance page renders.
BY_ID: dict[str, dict[str, Any]] = {
    "defaults": DEFAULTS,
    "profiles": PROFILES,
    "hero_meta": HERO_META,
    "cost_tables": COST_TABLES,
}
