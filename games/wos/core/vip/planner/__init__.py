"""VIP progression planner (calculator + coordinator domain).

Single linear track VIP 1 → 12 over the ladder in ``games/wos/db/vip_levels.yaml``.
:func:`plan_next` returns the next level-up (cost in ``vip_points``); :func:`vip_roadmap`
totals current→target and decomposes it into VIP Points items — the wostools VIP
calculator's answer. The live ``vip.level`` reader is ``sync_vip_level`` (games/wos/vip).
"""
from __future__ import annotations

from .model import VipData, VipLevel, load_vip_levels
from .planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    NONE,
    SELECTED,
    VipCandidate,
    VipPlan,
    VipRoadmap,
    plan_next,
    vip_roadmap,
    vip_value,
)

__all__ = [
    "INSUFFICIENT_RESOURCES",
    "LOCKED",
    "NONE",
    "SELECTED",
    "VipCandidate",
    "VipData",
    "VipLevel",
    "VipPlan",
    "VipRoadmap",
    "load_vip_levels",
    "plan_next",
    "vip_roadmap",
    "vip_value",
]
