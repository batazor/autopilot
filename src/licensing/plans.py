"""License plan catalog — the single source of truth for tiers, prices and the
features each tier unlocks.

In-game alliance ranks are R1–R5; we map paid tiers onto that vocabulary:

  - **R2** — Free. Base bot (scenarios, OCR, approvals).
  - **R3** — $5. Adds gift-code redemption for external accounts.
  - **R4** — $30 (alliance R4). Adds the Radar kingdom-map scanner.

Features are cumulative: a tier includes everything below it. ``tier`` on a
license is the plan id (``r2``/``r3``/``r4``); issuance resolves the feature
list from here so a higher tier always carries the lower tiers' features.
"""
from __future__ import annotations

from dataclasses import dataclass

# Feature flags checked by licensing.gate.has_feature / require_feature.
FEATURE_GIFT_EXTERNAL = "gift_codes.external_accounts"
FEATURE_RADAR = "radar"


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    price_usd: int
    features: tuple[str, ...]
    blurb: str


PLANS: tuple[Plan, ...] = (
    Plan(
        id="r2",
        label="R2 · Free",
        price_usd=0,
        features=(),
        blurb="Base bot: scenarios on your device, OCR and the approvals queue.",
    ),
    Plan(
        id="r3",
        label="R3 · $5",
        price_usd=5,
        features=(FEATURE_GIFT_EXTERNAL,),
        blurb="Adds gift-code redemption for external accounts (alliance / partner farms).",
    ),
    Plan(
        id="r4",
        label="R4 · $30",
        price_usd=30,
        features=(FEATURE_GIFT_EXTERNAL, FEATURE_RADAR),
        blurb="Alliance R4 — adds the Radar kingdom-map scanner.",
    ),
)

_BY_ID = {p.id: p for p in PLANS}


def plan_by_id(tier: str | None) -> Plan | None:
    return _BY_ID.get((tier or "").strip().lower())


def features_for_tier(tier: str | None) -> list[str]:
    """Canonical feature list for a plan id (empty for unknown tiers)."""
    plan = plan_by_id(tier)
    return list(plan.features) if plan else []
