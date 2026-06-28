"""Shared helpers for on-device readers that feed the value-greedy planners.

The investment planners (pets / charms / gear / hero_gear / island) consume a
per-account ``owned`` input that lives in the free-form ``planner`` dict on
``GamerState`` (see ``config.state_schema`` — the "annotation" layer), NOT a
declared schema field like ``troops`` / ``vip.level``. This package owns the one
correct way to persist + self-heal those inputs so every reader stays consistent.
"""
from games.wos.core.readers.parse import parse_owned_flat, parse_owned_nested
from games.wos.core.readers.persist import (
    collect_read_fields,
    overlay_durable_planner_owned,
    persist_planner_owned,
)

__all__ = [
    "collect_read_fields",
    "overlay_durable_planner_owned",
    "parse_owned_flat",
    "parse_owned_nested",
    "persist_planner_owned",
]
