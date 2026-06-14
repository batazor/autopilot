"""License plan catalog — the single source of truth for tiers, prices and the
per-tier caps.

In-game alliance ranks are R1–R5; we map paid tiers onto that vocabulary:

  - **R2** — Free. Base bot (scenarios, OCR, approvals).
  - **R3** — $5. Adds gift-code redemption for external accounts (cap: 5).
  - **R4** — $30 (alliance R4). Adds the Radar kingdom-map scanner and alliance
    statistics (cap: 50).

Tiers form a cumulative ladder ``r2 < r3 < r4``: a higher tier unlocks
everything the lower tiers do. Paid capabilities are gated by comparing the
license tier against a required minimum (see :func:`tier_at_least` and
``licensing.gate``), not by named feature flags.

Each tier also caps how many external gift-code accounts it may register
(``max_external_accounts``); the cap is enforced per game. R2 has the
external-accounts capability disabled, so its cap is 0.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tier ladder, low → high. Capability gates compare a license tier's rank
# (index in this tuple) against a required minimum. Unknown/legacy tier
# strings rank below r2 (rank -1), so they unlock no paid capability.
#
# ``r5`` is the internal owner/developer tier (in-game alliance R5 = leader).
# It is NOT a customer-facing plan: it exists to gate modules still in
# development behind an owner-only license so they stay hidden from every other
# tier. Top of the ladder, so it unlocks everything below it.
TIER_ORDER = ("r2", "r3", "r4", "r5")


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    price_usd: int
    blurb: str
    # Per-game cap on external gift-code accounts the tier may register.
    # 0 when the tier doesn't include the external-accounts capability.
    max_external_accounts: int = 0


PLANS: tuple[Plan, ...] = (
    Plan(
        id="r2",
        label="R2 · Free",
        price_usd=0,
        blurb="Base bot: scenarios on your device, OCR and the approvals queue.",
        max_external_accounts=0,
    ),
    Plan(
        id="r3",
        label="R3 · $5",
        price_usd=5,
        blurb="Adds gift-code redemption for up to 5 external accounts (alliance / partner farms).",
        max_external_accounts=5,
    ),
    Plan(
        id="r4",
        label="R4 · $30",
        price_usd=30,
        blurb="Alliance R4 — up to 50 external accounts, Radar, and alliance statistics.",
        max_external_accounts=50,
    ),
    Plan(
        id="r5",
        label="R5 · Owner",
        price_usd=0,
        blurb="Owner/developer tier — unlocks in-development modules. Not for sale.",
        max_external_accounts=999,
    ),
)

_BY_ID = {p.id: p for p in PLANS}


def plan_by_id(tier: str | None) -> Plan | None:
    return _BY_ID.get((tier or "").strip().lower())


def tier_rank(tier: str | None) -> int:
    """Rank of ``tier`` on the ladder — its index in :data:`TIER_ORDER`.

    Unknown / legacy tier strings (``free``, ``trial``, ``pro``, ``None``)
    return ``-1`` so they rank below the free tier and unlock nothing paid.
    """
    try:
        return TIER_ORDER.index((tier or "").strip().lower())
    except ValueError:
        return -1


def tier_at_least(tier: str | None, minimum: str) -> bool:
    """True iff ``tier`` ranks at or above ``minimum`` on the ladder.

    ``minimum`` must be a valid tier id; an invalid minimum returns ``False``.
    """
    min_rank = tier_rank(minimum)
    if min_rank < 0:
        return False
    return tier_rank(tier) >= min_rank


def external_accounts_limit_for_tier(tier: str | None) -> int:
    """Per-game external-account cap for a plan id (0 for unknown tiers)."""
    plan = plan_by_id(tier)
    return plan.max_external_accounts if plan else 0
