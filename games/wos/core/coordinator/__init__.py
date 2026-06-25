"""Cross-domain coordinator — the brain over the per-domain planners.

Arbitrates the shared resource pool and the parallel execution channels
(construction / research / march / training) across all domains under one
role-driven objective, so the bot acts as one coherent agent instead of N local
optimisers. Pure and testable; live readers + dispatch are deferred.
"""
from __future__ import annotations

from .adapters import (
    from_build_slate,
    from_charms_plan,
    from_gear_plan,
    from_hero_gear_plan,
    from_hero_plan,
    from_intel_plan,
    from_pet_plan,
    from_research_plan,
    from_training_plan,
    from_vip_plan,
)
from .allocate import coordinate_optimal
from .chief_orders import ChiefOrderPlan, recommend_orders
from .coordinator import coordinate
from .cycle import CyclePlan, plan_cycle
from .dailies import (
    DailyBias,
    DailyNudge,
    DailyTask,
    daily_bias,
    merge_boosts,
)
from .domains import (
    DOMAINS,
    DomainSpec,
    channel_kinds,
    dev_channels,
    investment_domain_names,
)
from .economy import (
    PRODUCER_BY_RESOURCE,
    EconomyBias,
    economy_bias,
    gather_candidates,
)
from .events import (
    EVENT_CATALOG,
    CalendarBias,
    EventWindow,
    HoldSignal,
    calendar_bias,
)
from .feedback import (
    ActionStat,
    FeedbackBias,
    FeedbackState,
    Outcome,
    apply_feedback,
    record,
    record_many,
    tuning,
)
from .march import intel_intent, march_channels, plan_march, timed_event_intent
from .model import (
    CHARM,
    CONSTRUCTION,
    GEAR,
    HERO,
    HERO_GEAR,
    MARCH,
    PET,
    RESEARCH,
    TRAINING,
    VIP,
    CandidateAction,
    Channel,
    Commit,
    CoordinatorDecision,
    Utility,
)
from .objective import DOMAIN_BAND, TRACK_DOMAIN, domain_priority
from .premium import (
    CurrencyPlan,
    CurrencySink,
    SpeedupApply,
    SpeedupPlan,
    SpeedupTask,
    allocate_currency,
    recommend_speedups,
)
from .projection import (
    CycleProjection,
    Milestone,
    ProjectedTask,
    project_cycle,
)
from .safety import (
    DefensiveAction,
    SafetyDirective,
    ThreatState,
    apply_safety,
    assess_safety,
)

__all__ = [
    "CHARM",
    "CONSTRUCTION",
    "DOMAINS",
    "DOMAIN_BAND",
    "EVENT_CATALOG",
    "GEAR",
    "HERO",
    "HERO_GEAR",
    "MARCH",
    "PET",
    "PRODUCER_BY_RESOURCE",
    "RESEARCH",
    "TRACK_DOMAIN",
    "TRAINING",
    "VIP",
    "ActionStat",
    "CalendarBias",
    "CandidateAction",
    "Channel",
    "ChiefOrderPlan",
    "Commit",
    "CoordinatorDecision",
    "CurrencyPlan",
    "CurrencySink",
    "CyclePlan",
    "CycleProjection",
    "DailyBias",
    "DailyNudge",
    "DailyTask",
    "DefensiveAction",
    "DomainSpec",
    "EconomyBias",
    "EventWindow",
    "FeedbackBias",
    "FeedbackState",
    "HoldSignal",
    "Milestone",
    "Outcome",
    "ProjectedTask",
    "SafetyDirective",
    "SpeedupApply",
    "SpeedupPlan",
    "SpeedupTask",
    "ThreatState",
    "Utility",
    "allocate_currency",
    "apply_feedback",
    "apply_safety",
    "assess_safety",
    "calendar_bias",
    "channel_kinds",
    "coordinate",
    "coordinate_optimal",
    "daily_bias",
    "dev_channels",
    "domain_priority",
    "economy_bias",
    "from_build_slate",
    "from_charms_plan",
    "from_gear_plan",
    "from_hero_gear_plan",
    "from_hero_plan",
    "from_intel_plan",
    "from_pet_plan",
    "from_research_plan",
    "from_training_plan",
    "from_vip_plan",
    "gather_candidates",
    "intel_intent",
    "investment_domain_names",
    "march_channels",
    "merge_boosts",
    "plan_cycle",
    "plan_march",
    "project_cycle",
    "recommend_orders",
    "recommend_speedups",
    "record",
    "record_many",
    "timed_event_intent",
    "tuning",
]
