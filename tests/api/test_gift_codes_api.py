"""Tests for gift-code dashboard service semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from api.services import gift_codes_api
from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer
from config.giftcodes_db import upsert_code
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_kingshot_view_does_not_expire_codes_from_calendar_date(
    sqlite_db: Path,
    mocker: MockerFixture,
) -> None:
    upsert_code(
        "PROTECTNATURE",
        game="kingshot",
        expires=datetime(2026, 6, 5, tzinfo=UTC),
    )
    registry = DeviceRegistry(
        devices=[
            DeviceEntry(
                name="bs1",
                profiles=(
                    DeviceProfile(
                        email="wos@example.test",
                        gamers=(Gamer(id=101, nickname="WosOne"),),
                        game="wos",
                    ),
                    DeviceProfile(
                        email="ks@example.test",
                        gamers=(Gamer(id=202, nickname="KingOne"),),
                        game="kingshot",
                    ),
                ),
                game="wos",
            )
        ]
    )
    mocker.patch.object(gift_codes_api, "load_devices", return_value=registry)
    mocker.patch.object(gift_codes_api, "_REPO", sqlite_db.parents[2])

    view = gift_codes_api.build_gift_codes_view(game="kingshot")

    assert view["player_ids"] == ["202"]
    assert view["metrics"]["expired"] == 0
    assert view["metrics"]["active"] == 1
    assert view["metrics"]["needs_run"] == 1
    assert view["active"][0]["code"] == "PROTECTNATURE"
    assert view["active"][0]["slot_expired"] is False
    assert view["active"][0]["needs_run"] is True
