"""Worker lifecycle enums (not game screen nodes).

Screen routing and taps live in :mod:`navigation.screen_graph`.
"""
from __future__ import annotations

from enum import StrEnum


class InstanceState(StrEnum):
    READY = "ready"
    BUSY = "busy"
    CRASHED = "crashed"
    RESTARTING = "restarting"
