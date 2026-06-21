"""Generic, game-agnostic saga engine for cross-account campaigns.

Pure compute (no Redis, no ``games/`` import): a :class:`CampaignDef` →
``Phase`` → ``Step`` + ``PhaseBarrier`` model and a deterministic
:func:`plan_campaign_tick`. WoS campaign definitions + the IO dispatcher that
runs this over the coord bus live in ``games/wos/core/fleet``.
"""
from __future__ import annotations

from .arbiter import (
    ArbitrationResult,
    ResourceClaim,
    arbitrate,
    arbitrate_optimal,
)
from .barrier import phase_outcome
from .device_scheduler import DeviceSchedule, Job, schedule_device
from .model import (
    ABORT,
    ADVANCE,
    ALL_REACHED,
    ANY_REACHED,
    DEADLINE_ONLY,
    ELAPSED,
    FLAG_SET,
    HOLD,
    TRIGGER_CALENDAR,
    TRIGGER_MANUAL,
    TRIGGER_NOTIFY,
    CampaignDecision,
    CampaignDef,
    CampaignRun,
    Participant,
    ParticipantStatus,
    Phase,
    PhaseBarrier,
    Step,
    StepDirective,
)
from .planner import plan_campaign_tick

__all__ = [
    "ABORT",
    "ADVANCE",
    "ALL_REACHED",
    "ANY_REACHED",
    "DEADLINE_ONLY",
    "ELAPSED",
    "FLAG_SET",
    "HOLD",
    "TRIGGER_CALENDAR",
    "TRIGGER_MANUAL",
    "TRIGGER_NOTIFY",
    "ArbitrationResult",
    "CampaignDecision",
    "CampaignDef",
    "CampaignRun",
    "DeviceSchedule",
    "Job",
    "Participant",
    "ParticipantStatus",
    "Phase",
    "PhaseBarrier",
    "ResourceClaim",
    "Step",
    "StepDirective",
    "arbitrate",
    "arbitrate_optimal",
    "phase_outcome",
    "plan_campaign_tick",
    "schedule_device",
]
