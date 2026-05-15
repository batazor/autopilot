"""Worker lifecycle enums (not game screen nodes).

Screen topology and taps live in :mod:`navigation.screen_graph` and
:mod:`navigation.screen_topology`.
"""
from __future__ import annotations

from enum import StrEnum


class InstanceState(StrEnum):
    READY = "ready"
    BUSY = "busy"
    CRASHED = "crashed"
    RESTARTING = "restarting"
