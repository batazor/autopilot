"""Bundle the scorer/candidate generator's static priors into one frozen object.

Values come from :mod:`config.balance._data` (baked-in Python). The legacy
``config/balance/*.yaml`` files were folded into that module so Nuitka can
compile them into ``config.so`` — see the module docstring for details.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from config.balance._data import COST_TABLES, DEFAULTS, HERO_META, PROFILES


@dataclass(frozen=True)
class BalanceContext:
    defaults: dict[str, Any]
    """``config/balance/defaults.yaml`` — sunkness / scarcity / threshold / solver."""
    profiles: dict[str, Any]
    """``profiles.profiles`` dict keyed by profile name."""
    active_profile_id: str
    """Name of the profile currently selected. Falls back to first entry
    if ``profiles.active`` is missing or unknown."""
    hero_defaults: dict[str, Any]
    """``hero_meta.defaults`` — applied to heroes not in ``overrides``."""
    hero_overrides: dict[str, dict[str, Any]]
    """``hero_meta.overrides`` — per-hero priors."""
    cost_tables: dict[str, dict[Any, Any]] = field(default_factory=dict)
    """All tables from ``cost_tables.yaml`` (``hero_xp_v1`` etc.). Inner keys are
    YAML-loaded (``int`` for ``skill_level_cap_by_star_v1`` star levels, ``str``
    elsewhere) — declared as ``Any`` to match the heterogeneous on-disk shape."""

    @property
    def active_profile(self) -> dict[str, Any]:
        return dict(self.profiles.get(self.active_profile_id) or {})

    def hero_meta(self, hero_id: str) -> dict[str, Any]:
        """Resolve hero priors: override merged onto defaults (shallow)."""
        merged: dict[str, Any] = dict(self.hero_defaults)
        override = self.hero_overrides.get(hero_id)
        if isinstance(override, dict):
            merged.update(override)
        return merged


@lru_cache(maxsize=1)
def load_balance_context() -> BalanceContext:
    # ``deepcopy`` so downstream mutation can't corrupt the baked-in module
    # globals (older callers occasionally pop / merge into the returned dicts).
    defaults = copy.deepcopy(DEFAULTS)
    profiles_doc = copy.deepcopy(PROFILES)
    hero_meta = copy.deepcopy(HERO_META)
    cost_tables = copy.deepcopy(COST_TABLES)

    profiles = dict(profiles_doc.get("profiles") or {})
    active = str(profiles_doc.get("active") or "").strip()
    if active not in profiles and profiles:
        active = next(iter(profiles))

    return BalanceContext(
        defaults=defaults,
        profiles=profiles,
        active_profile_id=active,
        hero_defaults=dict(hero_meta.get("defaults") or {}),
        hero_overrides=dict(hero_meta.get("overrides") or {}),
        cost_tables=cost_tables,
    )


def invalidate_balance_context() -> None:
    load_balance_context.cache_clear()
