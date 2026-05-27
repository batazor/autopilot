"""Tests for src/config/giftcodes_db.py — SQLite store for gift codes."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from games.wos.gift_codes.models import RedeemStatus

from config.giftcodes_db import (
    code_exists,
    count_codes,
    delete_code,
    get_redemption,
    list_codes,
    set_redemption,
    set_redemption_bulk,
    upsert_code,
)
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


# ---------------------------------------------------------------------------
# upsert + list
# ---------------------------------------------------------------------------


def test_upsert_new_code_then_list(sqlite_db: Path) -> None:
    upsert_code("CODE_A")
    codes = list_codes()
    assert len(codes) == 1
    assert codes[0].name == "CODE_A"
    assert codes[0].user_for == {}


def test_upsert_merges_fields(sqlite_db: Path) -> None:
    upsert_code("X", last_api_err_code=40007, last_api_msg="TIME ERROR")
    # Second call with only one field should not wipe the other.
    upsert_code("X", last_api_msg="UPDATED")
    code = next(c for c in list_codes() if c.name == "X")
    assert code.last_api_err_code == 40007
    assert code.last_api_msg == "UPDATED"


def test_upsert_persists_expires(sqlite_db: Path) -> None:
    expires = datetime(2026, 12, 31, tzinfo=UTC)
    upsert_code("EXP", expires=expires)
    code = next(c for c in list_codes() if c.name == "EXP")
    assert code.expires == expires


def test_count_and_exists(sqlite_db: Path) -> None:
    assert count_codes() == 0
    assert not code_exists("MISSING")
    upsert_code("FOUND")
    assert count_codes() == 1
    assert code_exists("FOUND")


# ---------------------------------------------------------------------------
# redemptions
# ---------------------------------------------------------------------------


def test_set_redemption_then_get(sqlite_db: Path) -> None:
    upsert_code("R")
    set_redemption("R", "1", RedeemStatus.SUCCESS)
    assert get_redemption("R", "1") == RedeemStatus.SUCCESS
    assert get_redemption("R", "missing") is None


def test_set_redemption_overwrites(sqlite_db: Path) -> None:
    upsert_code("R")
    set_redemption("R", "1", RedeemStatus.PENDING)
    set_redemption("R", "1", RedeemStatus.ALREADY_RECEIVED)
    assert get_redemption("R", "1") == RedeemStatus.ALREADY_RECEIVED


def test_set_redemption_bulk_stamps_all_players(sqlite_db: Path) -> None:
    upsert_code("DEAD")
    set_redemption_bulk("DEAD", ["1", "2", "3"], RedeemStatus.CDK_EXPIRED)
    for pid in ("1", "2", "3"):
        assert get_redemption("DEAD", pid) == RedeemStatus.CDK_EXPIRED


def test_set_redemption_bulk_empty_list_noop(sqlite_db: Path) -> None:
    upsert_code("Z")
    set_redemption_bulk("Z", [], RedeemStatus.CDK_EXPIRED)
    # nothing should have been written
    assert get_redemption("Z", "1") is None


def test_list_codes_aggregates_user_for(sqlite_db: Path) -> None:
    upsert_code("A")
    upsert_code("B")
    set_redemption("A", "1", RedeemStatus.SUCCESS)
    set_redemption("A", "2", RedeemStatus.FAILED)
    set_redemption("B", "1", RedeemStatus.CDK_EXPIRED)

    codes = {c.name: c for c in list_codes()}
    assert codes["A"].user_for == {
        "1": RedeemStatus.SUCCESS,
        "2": RedeemStatus.FAILED,
    }
    assert codes["B"].user_for == {"1": RedeemStatus.CDK_EXPIRED}


# ---------------------------------------------------------------------------
# delete cascades
# ---------------------------------------------------------------------------


def test_delete_code_cascades_redemptions(sqlite_db: Path) -> None:
    upsert_code("X")
    set_redemption("X", "1", RedeemStatus.SUCCESS)
    set_redemption("X", "2", RedeemStatus.FAILED)
    delete_code("X")
    assert not code_exists("X")
    assert get_redemption("X", "1") is None
    assert get_redemption("X", "2") is None
