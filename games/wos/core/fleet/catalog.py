"""The WoS cross-account campaign catalog — the three reference campaigns
expressed in the generic saga model.

All ship ``enabled=False``; the adapter overlays the real ``enabled`` + knobs
from ``fleet.yaml`` (master + per-campaign), mirroring ``coordinator/march.yaml``.
``run_scenario`` steps point at event scenarios; the recall/attack/reinforce/
switch steps are deferred (see :mod:`step_kinds`) until their on-device scenarios
land — flipping a placeholder here to a real key is the only change needed.
"""
from __future__ import annotations

from coord.campaign import (
    ABORT,
    ADVANCE,
    ALL_REACHED,
    ANY_REACHED,
    DEADLINE_ONLY,
    FLAG_SET,
    TRIGGER_CALENDAR,
    TRIGGER_MANUAL,
    TRIGGER_NOTIFY,
    CampaignDef,
    Phase,
    PhaseBarrier,
    Step,
)

from . import step_kinds as sk

# Calendar slug the joint-event campaign rides (matches calendar ``slug()`` /
# the coordinator EVENT_CATALOG: slug("Power Up") == "power_up").
JOINT_EVENT_ANCHOR = "power_up"


def _joint_event() -> CampaignDef:
    return CampaignDef(
        id="joint_event",
        title="Joint event participation",
        trigger=TRIGGER_CALENDAR,
        anchor_event_slug=JOINT_EVENT_ANCHOR,
        min_participants=2,
        max_participants=8,
        phases=(
            Phase(
                name="gather_points",
                steps=(Step(sk.RUN_SCENARIO, "all", "event.gather", requires_switch=True),),
                barrier=PhaseBarrier(
                    ALL_REACHED, signal="quota_reached", timeout_s=3600, on_timeout=ADVANCE
                ),
            ),
            Phase(
                name="converge",
                steps=(Step(sk.RUN_SCENARIO, "all", "event.rally", requires_switch=True),),
                barrier=PhaseBarrier(
                    ALL_REACHED, signal="joined", timeout_s=900, on_timeout=ADVANCE
                ),
            ),
            Phase(
                name="claim",
                steps=(Step(sk.RUN_SCENARIO, "all", "event.claim", requires_switch=True),),
                barrier=PhaseBarrier(DEADLINE_ONLY, timeout_s=300, on_timeout=ADVANCE),
            ),
        ),
    )


def _farm_raid() -> CampaignDef:
    resume = Step(sk.RUN_SCENARIO, "farm", "city.resume_troops")
    return CampaignDef(
        id="farm_raid",
        title="Farm raid (farm withdraws troops first)",
        trigger=TRIGGER_MANUAL,
        min_participants=2,
        max_participants=2,
        default_ttl_s=1800.0,
        phases=(
            Phase(
                name="farm_recall",
                steps=(Step(sk.RECALL, "farm", "city.recall_troops"),),
                # Safety gate: the fighter cannot start until the farm confirms
                # the city is empty; if it never does, abort + resume troops.
                barrier=PhaseBarrier(
                    FLAG_SET, signal="city_empty", timeout_s=600, on_timeout=ABORT
                ),
                rollback=(resume,),
            ),
            Phase(
                name="fighter_attack",
                steps=(
                    Step(
                        sk.ATTACK_COORDS, "fighter", "march.attack_coords",
                        requires_switch=True, params={"target": "farm_city"},
                    ),
                ),
                barrier=PhaseBarrier(
                    FLAG_SET, signal="attack_landed", timeout_s=300, on_timeout=ABORT
                ),
                rollback=(resume,),
            ),
            Phase(
                name="farm_resume",
                steps=(resume,),
                barrier=PhaseBarrier(DEADLINE_ONLY, timeout_s=120, on_timeout=ADVANCE),
            ),
        ),
    )


def _reinforcement() -> CampaignDef:
    return CampaignDef(
        id="reinforcement",
        title="Reinforce an ally under attack",
        trigger=TRIGGER_NOTIFY,
        min_participants=1,
        max_participants=2,
        default_ttl_s=600.0,  # time-critical
        phases=(
            Phase(
                name="send_troops",
                steps=(
                    Step(
                        sk.REINFORCE, "helper", "march.reinforce",
                        requires_switch=True, params={"target": "ally_under_attack"},
                    ),
                ),
                barrier=PhaseBarrier(
                    ANY_REACHED, signal="troops_sent", timeout_s=120, on_timeout=ADVANCE
                ),
            ),
        ),
    )


def build_campaign_defs() -> dict[str, CampaignDef]:
    """All campaigns, keyed by id, all ``enabled=False`` (overlaid from fleet.yaml)."""
    return {c.id: c for c in (_joint_event(), _farm_raid(), _reinforcement())}
