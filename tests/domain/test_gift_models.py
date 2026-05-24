"""Gift code model semantics — expiry, dead-API, redemption gating."""

from __future__ import annotations

import pytest

models = pytest.importorskip(
    "modules.gift_codes.models",
    reason="gift_codes module is in draft (modules/draft/gift_codes/) — skip until promoted",
)
GiftCode = models.GiftCode
RedeemStatus = models.RedeemStatus


def test_is_effectively_expired_calendar() -> None:
    c = GiftCode(name="X", expires="2099-01-01T00:00:00Z")
    assert not c.is_effectively_expired()


def test_is_effectively_expired_api_cdk_expired() -> None:
    c = GiftCode(
        name="X",
        user_for={"1": RedeemStatus.CDK_EXPIRED},
    )
    assert c.is_effectively_expired()
    assert c.is_api_slot_dead()


def test_needs_redemption_respects_cdk_not_found() -> None:
    c = GiftCode(name="X", user_for={"1": RedeemStatus.CDK_NOT_FOUND})
    assert not c.needs_redemption("1")
