"""Fire Crystal → Refined Fire Crystal weekly-conversion cost model (expected value).

Pure calculator over ``games/wos/db/rfc_conversion.yaml`` (re-encoded from
wostools.net/rfc-simulator). Turns an RFC requirement into an FC budget + a number of
weeks via the efficient Tier-1 pace; ``convert_path`` gives the EV of pushing tiers.
"""
from __future__ import annotations

from .conversion import (
    ConvertPath,
    RfcConversion,
    RfcPlan,
    RfcTier,
    WeeklyEfficient,
    convert_path,
    efficient_weekly,
    load_rfc_conversion,
    plan_for_rfc,
)

__all__ = [
    "ConvertPath",
    "RfcConversion",
    "RfcPlan",
    "RfcTier",
    "WeeklyEfficient",
    "convert_path",
    "efficient_weekly",
    "load_rfc_conversion",
    "plan_for_rfc",
]
