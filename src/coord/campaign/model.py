"""Pure, game-agnostic saga model — no Redis, no ``games/`` import.

One abstraction expresses every cross-account campaign:

    Campaign = ordered Phases
    Phase    = per-account Steps (each emits one directive) + a Barrier that
               gates advancing to the next phase

``Step.kind`` and ``PhaseBarrier.signal`` are opaque strings — the engine reads
only booleans/quorum; the WoS layer (``games/wos/core/fleet``) owns their
meaning. Frozen dataclasses mirroring ``coordinator.model`` style.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- Barrier kinds (how a phase decides it may advance) -----------------------
ALL_REACHED = "all_reached"      # every acting participant's signal is set
ANY_REACHED = "any_reached"      # at least one acting participant's signal is set
FLAG_SET = "flag_set"            # a single named signal is set (e.g. farm "city_empty")
ELAPSED = "elapsed"              # a min dwell has passed since the phase started
DEADLINE_ONLY = "deadline_only"  # advance purely when the phase timer elapses

# --- on_timeout policies ------------------------------------------------------
ABORT = "abort"      # roll back + abort the run (safety-critical phases)
ADVANCE = "advance"  # best-effort: move on regardless
HOLD = "hold"        # stay on the phase (don't advance, don't re-spam)

# --- Trigger kinds ------------------------------------------------------------
TRIGGER_CALENDAR = "calendar"
TRIGGER_NOTIFY = "notify"
TRIGGER_MANUAL = "manual"

# --- Run statuses -------------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
DONE = "done"
ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class PhaseBarrier:
    """Condition to advance from a phase to the next. Pure predicate inputs."""

    kind: str
    signal: str = ""               # per-participant flag the kind reads
    timeout_s: float | None = None  # phase deadline (None = no timeout)
    on_timeout: str = ABORT
    min_dwell_s: float = 0.0       # ELAPSED: seconds the phase must persist


@dataclass(frozen=True, slots=True)
class Step:
    """One directive emitted to the selected account(s) while its phase is active."""

    kind: str                      # WoS-owned: run_scenario|switch_player|recall|attack_coords|reinforce
    role_selector: str = "all"     # all | <role> | <fid>
    scenario: str = ""             # scenario key (or symbolic action)
    params: Mapping[str, str] = field(default_factory=dict)
    requires_switch: bool = False  # shared-device: sequence switch→act→switch


@dataclass(frozen=True, slots=True)
class Phase:
    name: str
    steps: tuple[Step, ...]
    barrier: PhaseBarrier
    rollback: tuple[Step, ...] = ()  # steps to run if the run aborts on this phase


@dataclass(frozen=True, slots=True)
class CampaignDef:
    """Declarative campaign template. Game-agnostic shape."""

    id: str
    title: str
    trigger: str                   # calendar | notify | manual
    phases: tuple[Phase, ...]
    anchor_event_slug: str = ""    # calendar trigger: the event window to ride
    min_participants: int = 1
    max_participants: int | None = None
    enabled: bool = False          # ships false; overlaid from fleet.yaml
    default_ttl_s: float = 3600.0  # whole-campaign deadline guard


# --- Runtime ------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Participant:
    fid: str
    role: str                      # farm | fighter | helper | balanced | …
    instance_id: str               # device the account maps to
    shares_device: bool = False    # >1 participant on this instance_id


@dataclass(frozen=True, slots=True)
class ParticipantStatus:
    fid: str
    reached: bool = False          # barrier signal satisfied for this participant
    last_directive_id: str = ""    # idempotency / in-flight tracking (the idem key)
    failed: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CampaignRun:
    """The live run (persisted/loaded by the adapter)."""

    campaign_id: str
    run_id: str                    # unique per activation (calendar window / notify id)
    phase_index: int
    status: str
    participants: tuple[Participant, ...]
    statuses: tuple[ParticipantStatus, ...]
    started_at: float
    phase_started_at: float
    deadline_at: float

    def status_for(self, fid: str) -> ParticipantStatus | None:
        for s in self.statuses:
            if s.fid == fid:
                return s
        return None


# --- Decision (planner output; mirrors CoordinatorDecision) -------------------
@dataclass(frozen=True, slots=True)
class StepDirective:
    """A concrete directive to post to one account this tick."""

    fid: str
    instance_id: str
    kind: str
    scenario: str
    params: Mapping[str, str]
    idempotency_key: str
    requires_switch: bool = False
    sequence_group: str = ""       # shared-device serialization token (= instance_id)
    sequence_order: int = 0


@dataclass(frozen=True, slots=True)
class CampaignDecision:
    """The tick's plan for one run: directives to post, barriers to ensure, moves."""

    directives: tuple[StepDirective, ...] = ()
    open_barriers: tuple[str, ...] = ()
    advance_to: int | None = None
    next_status: str = RUNNING
    updated_statuses: tuple[ParticipantStatus, ...] = ()
    trace: tuple[str, ...] = ()
