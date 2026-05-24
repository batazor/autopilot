"""Tests for gift code Pydantic models."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from modules.gift_codes.models import GiftCode, RedeemStatus


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def test_is_expired_returns_false_when_expires_missing() -> None:
    code = GiftCode(name="FREE100")
    assert code.is_expired() is False


def test_is_expired_treats_naive_datetime_as_utc() -> None:
    past = _now_utc() - timedelta(days=1)
    code = GiftCode(name="OLD", expires=past.replace(tzinfo=None))
    assert code.is_expired() is True


def test_is_expired_skips_sentinel_year_zero() -> None:
    sentinel = datetime(1970, 1, 1, tzinfo=UTC)
    code = GiftCode(name="UNKNOWN_EXPIRY", expires=sentinel)
    # Year < 2000 is the convention for "no expiry known" — must NOT be considered expired.
    assert code.is_expired() is False


def test_is_api_slot_dead_when_any_player_reports_expired() -> None:
    code = GiftCode(
        name="X",
        userFor={"1": RedeemStatus.SUCCESS, "2": RedeemStatus.CDK_EXPIRED},
    )
    assert code.is_api_slot_dead() is True


def test_is_api_slot_dead_when_not_found_anywhere() -> None:
    code = GiftCode(name="X", userFor={"1": RedeemStatus.CDK_NOT_FOUND})
    assert code.is_api_slot_dead() is True


def test_is_api_slot_dead_false_for_normal_statuses() -> None:
    code = GiftCode(
        name="X",
        userFor={
            "1": RedeemStatus.SUCCESS,
            "2": RedeemStatus.ALREADY_RECEIVED,
            "3": RedeemStatus.PENDING,
        },
    )
    assert code.is_api_slot_dead() is False


@pytest.mark.parametrize(
    ("status", "needs"),
    [
        (RedeemStatus.PENDING, True),
        (RedeemStatus.FAILED, True),
        (RedeemStatus.SUCCESS, False),
        (RedeemStatus.ALREADY_RECEIVED, False),
        (RedeemStatus.CDK_EXPIRED, False),
        (RedeemStatus.CDK_NOT_FOUND, False),
        (RedeemStatus.STOVE_LEVEL_TOO_LOW, False),
    ],
)
def test_needs_redemption_per_player_status(status: RedeemStatus, needs: bool) -> None:
    code = GiftCode(name="X", userFor={"player1": status})
    assert code.needs_redemption("player1") is needs


def test_needs_redemption_unknown_player_means_pending() -> None:
    code = GiftCode(name="X", userFor={"existing": RedeemStatus.SUCCESS})
    assert code.needs_redemption("brand_new") is True


def test_needs_redemption_false_when_code_effectively_expired() -> None:
    code = GiftCode(
        name="X",
        expires=_now_utc() - timedelta(hours=1),
        userFor={"player1": RedeemStatus.PENDING},
    )
    assert code.needs_redemption("player1") is False


def test_alias_round_trip_via_pydantic() -> None:
    """The model still accepts YAML-style alias keys (userFor / lastApiErrCode / lastApiMsg)
    — used by giftcodes_db.migrate_from_yaml during the one-shot import."""
    original = GiftCode(
        name="TESTCODE",
        userFor={"123": RedeemStatus.SUCCESS, "456": RedeemStatus.PENDING},
        lastApiErrCode=20000,
        lastApiMsg="ok",
    )
    revived = GiftCode.model_validate(original.model_dump(by_alias=True))
    assert revived.name == original.name
    assert revived.user_for == original.user_for
    assert revived.last_api_err_code == original.last_api_err_code
    assert revived.last_api_msg == original.last_api_msg
