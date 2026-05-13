"""Load ``config/balance/*.yaml`` into one bundle the scorer/candidate
generator can pass around without re-reading disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


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
    cost_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    """All tables from ``cost_tables.yaml`` (``hero_xp_v1`` etc.)."""

    @property
    def active_profile(self) -> dict[str, Any]:
        return dict(self.profiles.get(self.active_profile_id) or {})

    def hero_meta(self, hero_id: str) -> dict[str, Any]:
        """Resolve hero priors: override merged onto defaults (shallow)."""
        merged: dict[str, Any] = dict(self.hero_defaults)
        override = self.hero_overrides.get(hero_id)
        if isinstance(override, dict):
            for k, v in override.items():
                merged[k] = v
        return merged


@lru_cache(maxsize=1)
def load_balance_context() -> BalanceContext:
    base = _repo_root() / "config" / "balance"
    defaults = _load_yaml(base / "defaults.yaml")
    profiles_doc = _load_yaml(base / "profiles.yaml")
    hero_meta = _load_yaml(base / "hero_meta.yaml")
    cost_tables = _load_yaml(base / "cost_tables.yaml")

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
