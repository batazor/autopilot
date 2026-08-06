"""Tests for the RU («Белая мгла») Echofun web redeemer.

Covers the dialog-phrase → RedeemStatus classification, RU player collection
(local RU-package gamers + externals), and the redeem loop's persistence:
success stamping, dead-code propagation, dead-FID handling, and the graceful
skip when Playwright isn't installed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from century.gift_codes import wos_ru as ru
from century.gift_codes.echofun import EchofunUnavailable, PlayerNotFound, classify
from century.gift_codes.models import RedeemStatus
from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer
from config.giftcodes_db import (
    get_redemption,
    upsert_code,
    upsert_external_gamer,
)
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Iterator[Path]:
    """Redirect the SQLite store to a fresh per-test DB."""
    db_path = tmp_path / "db" / "state" / "wos.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


@pytest.fixture
def _no_sleep(mocker: MockerFixture) -> None:
    mocker.patch.object(ru.asyncio, "sleep", new=AsyncMock())


# ── classify ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Успешно! Заберите награды из игровой почты", RedeemStatus.SUCCESS),
        ("Награды уже получены", RedeemStatus.ALREADY_RECEIVED),
        ("Код можно использовать только один раз", RedeemStatus.ALREADY_RECEIVED),
        ("ID игрока не найден", RedeemStatus.ROLE_NOT_FOUND),
        ("Время действия кода истекло", RedeemStatus.CDK_EXPIRED),
        ("Достигнут лимит получения", RedeemStatus.CDK_EXPIRED),
        ("Код не найден", RedeemStatus.CDK_NOT_FOUND),
        ("Ошибка кода активации", RedeemStatus.CDK_NOT_FOUND),
        ("Требования не выполнены: уровень топки", RedeemStatus.STOVE_LEVEL_TOO_LOW),
        ("Сервер занят, попробуйте позже", RedeemStatus.FAILED),
        ("что-то совсем непонятное", None),
    ],
)
def test_classify_phrases(message: str, expected: RedeemStatus | None) -> None:
    assert classify(message) == expected


def test_player_not_found_wins_over_generic_invalid_code() -> None:
    # «ID игрока не найден» also contains «не найден» — the specific pattern
    # must be matched first (the lolka original had them the other way round).
    assert classify("ID игрока не найден") == RedeemStatus.ROLE_NOT_FOUND


# ── _collect_players ────────────────────────────────────────────────────────


def _registry() -> DeviceRegistry:
    return DeviceRegistry(
        devices=[
            DeviceEntry(
                name="bs5",
                profiles=(
                    DeviceProfile(
                        email="a@b.c",
                        gamers=(
                            Gamer(id=111, nickname="ru-one", game_package="com.gof.globalru"),
                            Gamer(id=222, nickname="global", game_package="com.gof.global"),
                        ),
                    ),
                ),
            ),
        ]
    )


def test_collect_players_filters_ru_package_and_adds_externals(
    sqlite_db: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_external_gamer(333, game="wos_ru", nickname="ext-ru")
    upsert_external_gamer(444, game="wos_ru", nickname="ext-off", enabled=False)
    upsert_external_gamer(555, game="wos", nickname="ext-global")

    player_ids, nicknames = ru._collect_players()

    assert player_ids == ["111", "333"]
    assert nicknames == {"111": "ru-one", "333": "ext-ru"}


# ── redeem loop ─────────────────────────────────────────────────────────────


class _FakeBrowser:
    """EchofunBrowser stand-in: canned lookups + scripted redeem replies."""

    def __init__(
        self,
        *,
        states: dict[str, str] | None = None,
        replies: dict[tuple[str, str], tuple[RedeemStatus, str]] | None = None,
        missing_fids: set[str] | None = None,
    ) -> None:
        self.states = states or {}
        self.replies = replies or {}
        self.missing_fids = missing_fids or set()
        self.redeem_calls: list[tuple[str, str, str]] = []
        self.closed = False

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def lookup_player(self, fid: str):
        from century.gift_codes.echofun import EchofunPlayer

        if fid in self.missing_fids:
            raise PlayerNotFound(fid)
        return EchofunPlayer(
            fid=fid, nickname=f"nick-{fid}", state=self.states.get(fid, "123"),
            furnace_level=30,
        )

    async def redeem(self, fid: str, state: str, code: str) -> tuple[RedeemStatus, str]:
        self.redeem_calls.append((fid, state, code))
        return self.replies.get((fid, code), (RedeemStatus.SUCCESS, "ok"))


@pytest.mark.asyncio
async def test_redeem_all_stamps_success_for_every_ru_player(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_external_gamer(333, game="wos_ru", nickname="ext-ru")
    upsert_code("RUCODE", game="wos_ru")

    fake = _FakeBrowser()
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)

    summary = await ru.run_gift_code_redeemer()

    assert {(r.player_id, r.status) for r in summary.results} == {
        ("111", RedeemStatus.SUCCESS),
        ("333", RedeemStatus.SUCCESS),
    }
    assert get_redemption("RUCODE", "111", game="wos_ru") == RedeemStatus.SUCCESS
    assert get_redemption("RUCODE", "333", game="wos_ru") == RedeemStatus.SUCCESS
    # state from the store lookup is what the form was filled with
    assert fake.redeem_calls == [("111", "123", "RUCODE"), ("333", "123", "RUCODE")]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_dead_code_is_propagated_to_all_players_and_not_retried(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_external_gamer(333, game="wos_ru", nickname="ext-ru")
    upsert_code("DEAD", game="wos_ru")

    fake = _FakeBrowser(
        replies={("111", "DEAD"): (RedeemStatus.CDK_EXPIRED, "истёк")}
    )
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)

    summary = await ru.run_gift_code_redeemer()

    # Only the first player was actually attempted; the second got the bulk stamp.
    assert fake.redeem_calls == [("111", "123", "DEAD")]
    assert get_redemption("DEAD", "111", game="wos_ru") == RedeemStatus.CDK_EXPIRED
    assert get_redemption("DEAD", "333", game="wos_ru") == RedeemStatus.CDK_EXPIRED
    attempted = {r.player_id: r.attempted for r in summary.results}
    assert attempted == {"111": True, "333": False}


@pytest.mark.asyncio
async def test_missing_fid_is_marked_role_not_found(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_code("RUCODE", game="wos_ru")

    fake = _FakeBrowser(missing_fids={"111"})
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)

    summary = await ru.run_gift_code_redeemer()

    assert fake.redeem_calls == []
    assert get_redemption("RUCODE", "111", game="wos_ru") == RedeemStatus.ROLE_NOT_FOUND
    assert [r.status for r in summary.results] == [RedeemStatus.ROLE_NOT_FOUND]


@pytest.mark.asyncio
async def test_redeemed_codes_are_skipped_on_the_next_run(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_code("RUCODE", game="wos_ru")

    fake = _FakeBrowser()
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)
    await ru.run_gift_code_redeemer()
    assert len(fake.redeem_calls) == 1

    summary = await ru.run_gift_code_redeemer()
    assert len(fake.redeem_calls) == 1  # nothing new attempted
    assert summary.results == []


@pytest.mark.asyncio
async def test_playwright_missing_degrades_to_empty_summary(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_code("RUCODE", game="wos_ru")

    fake = _FakeBrowser()
    fake.start = AsyncMock(side_effect=EchofunUnavailable("no playwright"))
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)

    summary = await ru.run_gift_code_redeemer()

    assert summary.results == []
    assert get_redemption("RUCODE", "111", game="wos_ru") is None


@pytest.mark.asyncio
async def test_redeem_for_player_scopes_to_one_fid(
    sqlite_db: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(ru, "load_devices", return_value=_registry())
    upsert_external_gamer(333, game="wos_ru", nickname="ext-ru")
    upsert_code("RUCODE", game="wos_ru")

    fake = _FakeBrowser()
    mocker.patch.object(ru, "EchofunBrowser", return_value=fake)

    summary = await ru.run_gift_code_redeemer_for_player(333)

    assert fake.redeem_calls == [("333", "123", "RUCODE")]
    assert [r.player_id for r in summary.results] == ["333"]
    assert get_redemption("RUCODE", "111", game="wos_ru") is None
