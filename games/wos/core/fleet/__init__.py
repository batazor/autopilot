"""WoS cross-account campaign layer: the campaign catalog + handlers consumed by
the scheduler's fleet orchestrator (``_run_fleet_coordinator``).

The generic saga engine lives in :mod:`coord.campaign`; this package supplies the
WoS-specific campaign definitions, step→scenario mapping, participant selection,
and barrier-signal readers, plus the IO adapter that runs it over the coord bus.
"""
from __future__ import annotations

from .catalog import build_campaign_defs
from .participants import Candidate, select_participants

__all__ = [
    "Candidate",
    "build_campaign_defs",
    "select_participants",
]
