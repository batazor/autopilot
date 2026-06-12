"""Tests for gift-code dashboard service semantics."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from api.services import gift_codes_api
from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer
from config.giftcodes_db import upsert_code
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from pytest_mock import MockerFixture


class _Summary:
    def __init__(self, *, total: int) -> None:
        self.results = [object() for _ in range(total)]

    def counts_by_status(self) -> dict[str, int]:
        return {"SUCCESS": len(self.results)} if self.results else {}


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


@pytest.mark.asyncio
async def test_startup_cycle_uses_scheduler_ttl_gate(
    mocker: MockerFixture,
) -> None:
    calls: list[str] = []

    async def _poll_wos() -> list[str]:
        calls.append("wos")
        return ["WOS1"]

    async def _poll_kingshot() -> list[str]:
        calls.append("kingshot")
        return []

    games = {
        "wos": gift_codes_api._GiftCodeGame(
            game="wos",
            redeem_lock_key="lock:wos",
            poll_once=_poll_wos,
            run_redeemer=mocker.AsyncMock(return_value=_Summary(total=1)),
        ),
        "kingshot": gift_codes_api._GiftCodeGame(
            game="kingshot",
            redeem_lock_key="lock:kingshot",
            poll_once=_poll_kingshot,
            run_redeemer=mocker.AsyncMock(return_value=_Summary(total=0)),
        ),
    }
    mocker.patch.object(gift_codes_api, "_GIFT_CODE_GAMES", games)

    class _FakeRedis:
        def __init__(self) -> None:
            self.keys: set[str] = set()
            self.set_calls: list[tuple[str, str, bool, int]] = []

        async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
            self.set_calls.append((key, value, nx, ex))
            if nx and key in self.keys:
                return False
            self.keys.add(key)
            return True

        async def eval(self, _script: str, _num_keys: int, key: str, token: str) -> int:
            del token
            self.keys.discard(key)
            return 1

    redis = _FakeRedis()

    first = await gift_codes_api.startup_scrape_gift_codes_once(redis, ttl_s=123)
    second = await gift_codes_api.startup_scrape_gift_codes_once(redis, ttl_s=123)

    assert calls == ["wos", "kingshot"]
    assert first == {
        "wos": {
            "status": "done",
            "new_codes": ["WOS1"],
            "count": 1,
            "redeem_total": 1,
            "redeem_counts": {"SUCCESS": 1},
        },
        "kingshot": {
            "status": "done",
            "new_codes": [],
            "count": 0,
            "redeem_total": 0,
            "redeem_counts": {},
        },
    }
    assert second == {
        "wos": {"status": "skipped", "reason": "ttl"},
        "kingshot": {"status": "skipped", "reason": "ttl"},
    }
    cadence_calls = [
        call for call in redis.set_calls if call[0].startswith("wos:scheduler:gift_codes_poll:")
    ]
    lock_calls = [call for call in redis.set_calls if call[0].startswith("lock:")]
    assert all(call[2:] == (True, 123) for call in cadence_calls)
    assert all(call[2:] == (True, gift_codes_api._GIFT_CODE_LOCK_TTL_SECONDS) for call in lock_calls)


@pytest.mark.asyncio
async def test_manual_scrape_and_redeem_dispatch_by_game(
    mocker: MockerFixture,
) -> None:
    poll = mocker.AsyncMock(return_value=["KS1"])
    run_redeemer = mocker.AsyncMock(return_value=_Summary(total=2))
    games = {
        "kingshot": gift_codes_api._GiftCodeGame(
            game="kingshot",
            redeem_lock_key="lock:kingshot",
            poll_once=poll,
            run_redeemer=run_redeemer,
        ),
    }
    mocker.patch.object(gift_codes_api, "_GIFT_CODE_GAMES", games)

    class _FakeRedis:
        async def set(self, *_args: object, **_kwargs: object) -> bool:
            return True

        async def eval(self, *_args: object) -> int:
            return 1

    @asynccontextmanager
    async def _fake_redis() -> AsyncIterator[_FakeRedis]:
        yield _FakeRedis()

    mocker.patch.object(gift_codes_api, "_api_gift_code_redis", _fake_redis)

    new = await gift_codes_api.scrape_gift_codes_for_game("kingshot")
    redeem = await gift_codes_api.redeem_gift_codes(game="kingshot")

    assert new == ["KS1"]
    assert redeem == {
        "ok": True,
        "game": "kingshot",
        "total": 2,
        "counts": {"SUCCESS": 2},
    }
