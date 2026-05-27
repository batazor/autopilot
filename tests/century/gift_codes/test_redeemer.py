"""Tests for ``modules.gift_codes.redeemer`` — helpers + the redeem state machine."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from century.api import CenturyAPIError, ErrCode
from century.gift_codes import wos as redeemer
from century.gift_codes.models import RedeemStatus
from century.gift_codes.wos import (
    GiftCodeRedeemer,
    GiftRedeemResult,
    GiftRedeemSummary,
    _captcha_backoff,
    _ec_to_status,
    _is_shutdown_error,
    _is_too_frequent_error,
    _jittered,
    run_gift_code_redeemer,
)
from config.devices import DeviceEntry, DeviceProfile, DeviceRegistry, Gamer
from config.giftcodes_db import get_redemption, list_codes, upsert_code
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    """Redirect the SQLite store to a fresh per-test DB."""
    db_path = tmp_path / "db" / "state" / "wos.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


# ── Pure helpers ────────────────────────────────────────────────────────────


def test_jittered_returns_zero_for_non_positive_input() -> None:
    assert _jittered(0.0) == 0.0
    assert _jittered(-5.0) == 0.0


def test_jittered_stays_within_25_percent_band() -> None:
    base = 10.0
    samples = [_jittered(base) for _ in range(200)]
    assert all(7.5 <= s <= 12.5 for s in samples), f"out of band: {samples!r}"


def test_captcha_backoff_grows_exponentially_within_jitter() -> None:
    # Centres are 8 → 16 → 32; ±25% bands must not overlap.
    s1 = _captcha_backoff(1)
    s2 = _captcha_backoff(2)
    s3 = _captcha_backoff(3)
    assert 6.0 <= s1 <= 10.0
    assert 12.0 <= s2 <= 20.0
    assert 24.0 <= s3 <= 40.0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Too Frequent", True),
        ("captcha too frequent, retry later", True),
        ("captcha bad", False),
        ("", False),
    ],
)
def test_is_too_frequent_error_detects_substring(message: str, expected: bool) -> None:
    assert _is_too_frequent_error(CenturyAPIError(message)) is expected


def test_is_shutdown_error_matches_known_markers() -> None:
    assert _is_shutdown_error(RuntimeError("cannot schedule new futures after shutdown")) is True
    assert _is_shutdown_error(RuntimeError("Event loop is closed")) is True


def test_is_shutdown_error_ignores_other_runtime_errors() -> None:
    assert _is_shutdown_error(RuntimeError("just a normal failure")) is False


def test_is_shutdown_error_ignores_non_runtime_exceptions() -> None:
    assert _is_shutdown_error(CenturyAPIError("loop is closed")) is False


@pytest.mark.parametrize(
    ("ec", "status"),
    [
        (ErrCode.SUCCESS, RedeemStatus.SUCCESS),
        (ErrCode.ALREADY_RECEIVED_1, RedeemStatus.ALREADY_RECEIVED),
        (ErrCode.ALREADY_RECEIVED_2, RedeemStatus.ALREADY_RECEIVED),
        (ErrCode.ALREADY_RECEIVED_3, RedeemStatus.ALREADY_RECEIVED),
        (ErrCode.CDK_EXPIRED, RedeemStatus.CDK_EXPIRED),
        (ErrCode.CDK_NOT_FOUND, RedeemStatus.CDK_NOT_FOUND),
        (ErrCode.STOVE_LEVEL_TOO_LOW, RedeemStatus.STOVE_LEVEL_TOO_LOW),
        (ErrCode.CAPTCHA_TOO_FREQUENT, RedeemStatus.FAILED),
        (ErrCode.CAPTCHA_ERROR, RedeemStatus.FAILED),
    ],
)
def test_ec_to_status_mapping(ec: ErrCode, status: RedeemStatus) -> None:
    assert _ec_to_status(ec) == status


# ── Summary aggregation ─────────────────────────────────────────────────────


def _result(status: RedeemStatus, *, code: str = "X", pid: str = "1") -> GiftRedeemResult:
    return GiftRedeemResult(code=code, player_id=pid, nickname=pid, status=status)


def test_summary_counts_group_by_status() -> None:
    summary = GiftRedeemSummary()
    summary.add(_result(RedeemStatus.SUCCESS))
    summary.add(_result(RedeemStatus.SUCCESS))
    summary.add(_result(RedeemStatus.ALREADY_RECEIVED))
    summary.add(_result(RedeemStatus.FAILED))

    assert summary.counts_by_status() == {
        "ALREADY_RECEIVED": 1,
        "FAILED": 1,
        "SUCCESS": 2,
    }


def test_summary_to_dict_includes_total_and_results() -> None:
    summary = GiftRedeemSummary()
    summary.add(_result(RedeemStatus.SUCCESS, code="A"))
    out = summary.to_dict()
    assert out["total"] == 1
    assert out["counts"] == {"SUCCESS": 1}
    assert out["results"][0]["code"] == "A"


def test_result_to_dict_strips_optional_none_fields() -> None:
    out = _result(RedeemStatus.PENDING).to_dict()
    assert "api_err_code" not in out
    assert "api_msg" not in out
    assert out["status"] == "PENDING"


def test_result_to_dict_keeps_api_fields_when_present() -> None:
    out = GiftRedeemResult(
        code="X",
        player_id="1",
        nickname="player",
        status=RedeemStatus.FAILED,
        api_err_code=40103,
        api_msg="captcha error",
    ).to_dict()
    assert out["api_err_code"] == 40103
    assert out["api_msg"] == "captcha error"


# ── _redeem_one state machine ───────────────────────────────────────────────


def _client_mock(
    *,
    player_ok: bool = True,
    captcha_calls: list[object] | None = None,
    redeem_calls: list[object] | None = None,
) -> MagicMock:
    client = MagicMock()
    client.fetch_player = (
        AsyncMock(return_value=MagicMock())
        if player_ok
        else AsyncMock(side_effect=CenturyAPIError("login failed"))
    )
    client.fetch_captcha = AsyncMock(
        side_effect=captcha_calls or [MagicMock(img_b64="data:img")]
    )
    client.redeem = AsyncMock(side_effect=redeem_calls or [(ErrCode.SUCCESS, "ok")])
    return client


@pytest.fixture
def _no_sleep(mocker: MockerFixture) -> None:
    """Strip out the inter-attempt sleeps that would slow tests to a crawl."""
    mocker.patch.object(redeemer.asyncio, "sleep", new=AsyncMock())


@pytest.mark.asyncio
async def test_redeem_one_returns_success_on_first_attempt(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock()

    status, ec, msg = await r._redeem_one(123, "CODE")

    assert status == RedeemStatus.SUCCESS
    assert ec == ErrCode.SUCCESS.value
    assert msg == "ok"
    assert r._client.fetch_captcha.await_count == 1
    assert r._client.redeem.await_count == 1


@pytest.mark.asyncio
async def test_redeem_one_returns_failed_when_login_fails(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock(player_ok=False)

    status, ec, msg = await r._redeem_one(123, "CODE")

    assert status == RedeemStatus.FAILED
    assert ec is None and msg is None
    r._client.fetch_captcha.assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_one_retries_then_succeeds_on_captcha_error(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock(
        captcha_calls=[MagicMock(img_b64="img1"), MagicMock(img_b64="img2")],
        redeem_calls=[(ErrCode.CAPTCHA_ERROR, ""), (ErrCode.SUCCESS, "ok")],
    )

    status, ec, _msg = await r._redeem_one(123, "CODE")

    assert status == RedeemStatus.SUCCESS
    assert ec == ErrCode.SUCCESS.value
    assert r._client.fetch_captcha.await_count == 2
    assert r._client.redeem.await_count == 2


@pytest.mark.asyncio
async def test_redeem_one_gives_up_after_max_captcha_retries(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    captcha = [MagicMock(img_b64=f"img{i}") for i in range(redeemer._MAX_CAPTCHA_RETRIES)]
    r._client = _client_mock(
        captcha_calls=captcha,
        redeem_calls=[(ErrCode.CAPTCHA_ERROR, "")] * redeemer._MAX_CAPTCHA_RETRIES,
    )

    status, ec, msg = await r._redeem_one(123, "CODE")

    assert status == RedeemStatus.FAILED
    assert ec is None and msg is None
    assert r._client.redeem.await_count == redeemer._MAX_CAPTCHA_RETRIES


@pytest.mark.asyncio
async def test_redeem_one_reraises_shutdown_error_from_login(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock()
    r._client.fetch_player = AsyncMock(side_effect=RuntimeError("Event loop is closed"))

    with pytest.raises(RuntimeError, match="Event loop is closed"):
        await r._redeem_one(123, "CODE")


@pytest.mark.asyncio
async def test_redeem_one_reraises_shutdown_error_from_redeem(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock()
    r._client.redeem = AsyncMock(
        side_effect=RuntimeError("cannot schedule new futures after shutdown")
    )

    with pytest.raises(RuntimeError, match="cannot schedule"):
        await r._redeem_one(123, "CODE")


@pytest.mark.asyncio
async def test_redeem_one_returns_failed_when_captcha_solver_raises(
    tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    mocker.patch.object(redeemer, "solve_captcha", side_effect=RuntimeError("no model"))
    r = GiftCodeRedeemer()
    r._client = _client_mock()

    status, ec, msg = await r._redeem_one(123, "CODE")

    assert status == RedeemStatus.FAILED
    assert ec is None and msg is None
    r._client.redeem.assert_not_awaited()


# ── redeem_all integration ──────────────────────────────────────────────────


def _registry(*ids: int) -> DeviceRegistry:
    profile = DeviceProfile(
        email="user@example.com",
        gamers=tuple(Gamer(id=pid, nickname=f"P{pid}") for pid in ids),
    )
    return DeviceRegistry(devices=[DeviceEntry(name="bs1", profiles=(profile,))])


@pytest.mark.asyncio
async def test_redeem_all_propagates_cdk_not_found_to_every_player(
    sqlite_db: Path, tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    upsert_code("DEAD")
    mocker.patch.object(redeemer, "load_devices", return_value=_registry(1, 2, 3))
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock(redeem_calls=[(ErrCode.CDK_NOT_FOUND, "not found")])

    summary = await r.redeem_all()

    # Only one API call (first player); but every player gets a result row.
    assert r._client.redeem.await_count == 1
    assert {res.player_id for res in summary.results} == {"1", "2", "3"}
    assert all(res.status == RedeemStatus.CDK_NOT_FOUND for res in summary.results)
    # First player was the actual attempter; others are marked attempted=False.
    attempted = [res for res in summary.results if res.attempted]
    assert len(attempted) == 1 and attempted[0].player_id == "1"
    # SQLite bulk-stamp: every player row marked terminal.
    for pid in ("1", "2", "3"):
        assert get_redemption("DEAD", pid) == RedeemStatus.CDK_NOT_FOUND


@pytest.mark.asyncio
async def test_redeem_all_persists_each_player_status(
    sqlite_db: Path, tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    upsert_code("FREE100")
    mocker.patch.object(redeemer, "load_devices", return_value=_registry(1, 2))
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")
    r = GiftCodeRedeemer()
    r._client = _client_mock(
        captcha_calls=[MagicMock(img_b64="img1"), MagicMock(img_b64="img2")],
        redeem_calls=[(ErrCode.SUCCESS, "ok"), (ErrCode.ALREADY_RECEIVED_1, "dup")],
    )

    summary = await r.redeem_all()

    codes = list_codes()
    assert len(codes) == 1
    assert codes[0].user_for == {
        "1": RedeemStatus.SUCCESS,
        "2": RedeemStatus.ALREADY_RECEIVED,
    }
    assert summary.counts_by_status() == {"ALREADY_RECEIVED": 1, "SUCCESS": 1}


@pytest.mark.asyncio
async def test_redeem_all_skips_codes_with_no_pending_players(
    sqlite_db: Path, tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    from config.giftcodes_db import set_redemption

    upsert_code("DONE")
    set_redemption("DONE", "1", RedeemStatus.SUCCESS)
    set_redemption("DONE", "2", RedeemStatus.SUCCESS)
    mocker.patch.object(redeemer, "load_devices", return_value=_registry(1, 2))
    r = GiftCodeRedeemer()
    r._client = _client_mock()

    summary = await r.redeem_all()

    assert summary.results == []
    r._client.fetch_player.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_gift_code_redeemer_returns_summary(
    sqlite_db: Path, tmp_path: Path, mocker: MockerFixture, _no_sleep: None
) -> None:
    upsert_code("X")
    mocker.patch.object(redeemer, "load_devices", return_value=_registry(1))
    mocker.patch.object(redeemer, "solve_captcha", return_value="ABCD")

    fake_client = _client_mock(redeem_calls=[(ErrCode.SUCCESS, "ok")])
    mocker.patch.object(redeemer, "CenturyClient", return_value=fake_client)

    summary = await run_gift_code_redeemer()

    assert summary.counts_by_status() == {"SUCCESS": 1}
