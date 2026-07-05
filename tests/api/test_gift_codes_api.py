"""Tests for gift-code dashboard service semantics."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import gift_codes as gift_codes_router
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


def test_beta_view_marks_codes_as_manual_in_game(sqlite_db: Path) -> None:
    upsert_code("BETA123", game="wos_beta")

    view = gift_codes_api.build_gift_codes_view(game="wos_beta")

    assert view["redeem_supported"] is False
    assert view["apply_mode"] == "in_game_player"
    assert view["active"][0]["needs_run"] is False


def test_wos_ru_registered_as_manual_in_game_source() -> None:
    spec = gift_codes_api._GIFT_CODE_GAMES["wos_ru"]

    assert spec.manual_source is True
    assert spec.redeem_supported is False
    assert spec.apply_mode == "in_game_player"


def test_wos_ru_view_is_manual_source(sqlite_db: Path) -> None:
    upsert_code("RUCODE1", game="wos_ru")

    view = gift_codes_api.build_gift_codes_view(game="wos_ru")

    assert view["manual_source"] is True
    assert view["redeem_supported"] is False
    assert view["apply_mode"] == "in_game_player"
    assert view["active"][0]["code"] == "RUCODE1"


def test_wos_view_is_not_manual_source(sqlite_db: Path) -> None:
    view = gift_codes_api.build_gift_codes_view(game="wos")

    assert view["manual_source"] is False


def test_add_manual_code_inserts_and_is_idempotent(sqlite_db: Path) -> None:
    from config.giftcodes_db import code_exists, list_codes

    first = gift_codes_api.add_manual_code(game="wos_ru", code="  RUCODE2 ")

    assert first == {"ok": True, "game": "wos_ru", "code": "RUCODE2", "created": True}
    assert code_exists("RUCODE2", game="wos_ru")
    assert [c.name for c in list_codes(game="wos_ru")] == ["RUCODE2"]

    again = gift_codes_api.add_manual_code(game="wos_ru", code="RUCODE2")

    assert again["created"] is False  # already present → idempotent


@pytest.mark.parametrize("bad", ["", "   ", "AB CD", "AB\tCD"])
def test_add_manual_code_rejects_blank_or_spaced_codes(sqlite_db: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="code"):
        gift_codes_api.add_manual_code(game="wos_ru", code=bad)


def test_add_manual_code_rejects_unknown_game(sqlite_db: Path) -> None:
    with pytest.raises(ValueError, match="unknown gift-code game"):
        gift_codes_api.add_manual_code(game="nope", code="X")


def test_delete_manual_code_removes(sqlite_db: Path) -> None:
    from config.giftcodes_db import code_exists

    gift_codes_api.add_manual_code(game="wos_ru", code="RUCODE3")
    assert code_exists("RUCODE3", game="wos_ru")

    result = gift_codes_api.delete_manual_code(game="wos_ru", code="RUCODE3")

    assert result == {"ok": True, "game": "wos_ru", "code": "RUCODE3"}
    assert not code_exists("RUCODE3", game="wos_ru")


def _gift_codes_client() -> TestClient:
    app = FastAPI()
    app.include_router(gift_codes_router.router)
    return TestClient(app)


def test_manual_code_routes_add_view_and_delete(sqlite_db: Path) -> None:
    client = _gift_codes_client()

    added = client.post("/api/gift-codes/codes?game=wos_ru", json={"code": "ROUTE1"})
    assert added.status_code == 200
    assert added.json() == {
        "ok": True,
        "game": "wos_ru",
        "code": "ROUTE1",
        "created": True,
    }

    view = client.get("/api/gift-codes?game=wos_ru").json()
    assert view["manual_source"] is True
    assert any(row["code"] == "ROUTE1" for row in view["active"])

    removed = client.delete("/api/gift-codes/codes/ROUTE1?game=wos_ru")
    assert removed.status_code == 200
    assert removed.json() == {"ok": True, "game": "wos_ru", "code": "ROUTE1"}

    after = client.get("/api/gift-codes?game=wos_ru").json()
    assert not any(row["code"] == "ROUTE1" for row in after["active"])


def test_add_code_route_rejects_blank_with_400(sqlite_db: Path) -> None:
    client = _gift_codes_client()

    resp = client.post("/api/gift-codes/codes?game=wos_ru", json={"code": "   "})

    assert resp.status_code == 400


def test_discord_config_is_saved_without_exposing_token(sqlite_db: Path) -> None:
    view = gift_codes_api.update_discord_config(
        bot_token="discord-token",
    )

    assert view == {
        "token_configured": True,
        "token_source": "ui",
        "wos_beta_channel_id": "1511081143083077652",
        "wos_beta_channel_source": "built_in",
        "kingshot_beta_channel_id": "1513031288695558285",
        "kingshot_beta_channel_source": "built_in",
    }
    assert "discord-token" not in str(view)

    cleared = gift_codes_api.update_discord_config(clear_token=True)

    assert cleared["token_configured"] is False
    assert cleared["token_source"] == "none"
    assert cleared["wos_beta_channel_id"] == "1511081143083077652"


@pytest.mark.asyncio
async def test_beta_redeem_is_not_supported() -> None:
    result = await gift_codes_api.redeem_gift_codes(game="wos_beta")

    assert result == {
        "ok": False,
        "game": "wos_beta",
        "redeem_supported": False,
        "reason": "beta_codes_apply_in_game_for_current_player",
    }


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
