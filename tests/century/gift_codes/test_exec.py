"""Tests for ``century.gift_codes.exec`` — DSL handlers and lock/state helpers.

The exec module collapses WOS + Kingshot scaffolding behind ``_GameSpec``
adapters, so these tests pin the behavior of the shared helpers and walk
the WOS handler end-to-end. Kingshot reuses the same code path; per-game
identity is verified by spec routing in ``test_specs_cover_wos_and_kingshot``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from century.gift_codes import exec as gc_exec
from tasks.dsl_exec import DslExecContext

if TYPE_CHECKING:
    import asyncio

    from pytest_mock import MockerFixture


def _ctx(redis_client: object | None) -> DslExecContext:
    return DslExecContext(
        redis_client=redis_client,
        player_id="player1",
        instance_id="bs1",
    )


def _fake_redis() -> MagicMock:
    r = MagicMock()
    r.set = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock(return_value=1)
    r.hset = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    return r


def _wos_spec() -> gc_exec._GameSpec:
    return next(s for s in gc_exec._SPECS if s.game_id == "wos")


# ── Spec registry ──────────────────────────────────────────────────────────


def test_specs_cover_wos_and_kingshot() -> None:
    ids = {s.game_id for s in gc_exec._SPECS}
    assert ids == {"wos", "kingshot"}

    handlers = gc_exec.DSL_EXEC_HANDLERS
    for name in (
        "gift_code_scrape",
        "gift_code_redeem",
        "kingshot_gift_code_scrape",
        "kingshot_gift_code_redeem",
    ):
        assert name in handlers
        assert callable(handlers[name])


# ── _acquire_lock ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acquire_lock_uses_nx_with_ttl_and_returns_true_on_success() -> None:
    r = _fake_redis()
    r.set.return_value = True

    ok = await gc_exec._acquire_lock(r, "wos:gift_code_redeem:lock", "abc")

    assert ok is True
    r.set.assert_awaited_once_with(
        "wos:gift_code_redeem:lock",
        "abc",
        nx=True,
        ex=gc_exec._LOCK_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_acquire_lock_returns_false_when_already_held() -> None:
    r = _fake_redis()
    r.set.return_value = None  # NX collision

    ok = await gc_exec._acquire_lock(r, "wos:gift_code_redeem:lock", "abc")

    assert ok is False


@pytest.mark.asyncio
async def test_acquire_lock_returns_false_on_redis_error() -> None:
    r = _fake_redis()
    r.set.side_effect = RuntimeError("network down")

    ok = await gc_exec._acquire_lock(r, "wos:gift_code_redeem:lock", "abc")

    assert ok is False


@pytest.mark.asyncio
async def test_acquire_lock_without_redis_uses_background_set(
    mocker: MockerFixture,
) -> None:
    # Empty background set → no in-flight task → lock granted (test-mode fallback).
    mocker.patch.object(gc_exec, "_BACKGROUND_GIFT_REDEEM_TASKS", set())
    assert await gc_exec._acquire_lock(None, "ignored", "abc") is True

    # Background task pending → denied.
    pending = MagicMock()
    pending.done.return_value = False
    mocker.patch.object(gc_exec, "_BACKGROUND_GIFT_REDEEM_TASKS", {pending})
    assert await gc_exec._acquire_lock(None, "ignored", "abc") is False


# ── _release_lock ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_lock_deletes_only_when_token_matches() -> None:
    r = _fake_redis()
    r.get.return_value = "abc"  # owned by us

    await gc_exec._release_lock(r, "wos:gift_code_redeem:lock", "abc")

    r.delete.assert_awaited_once_with("wos:gift_code_redeem:lock")


@pytest.mark.asyncio
async def test_release_lock_skips_delete_when_owned_by_other_token() -> None:
    r = _fake_redis()
    r.get.return_value = "stolen"

    await gc_exec._release_lock(r, "wos:gift_code_redeem:lock", "abc")

    r.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_lock_swallows_redis_errors() -> None:
    r = _fake_redis()
    r.get.side_effect = RuntimeError("flap")

    # Best-effort cleanup — must not raise.
    await gc_exec._release_lock(r, "wos:gift_code_redeem:lock", "abc")


@pytest.mark.asyncio
async def test_release_lock_noop_without_redis() -> None:
    await gc_exec._release_lock(None, "ignored", "abc")  # no exception


# ── _write_state ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_state_writes_hash_and_sets_ttl() -> None:
    r = _fake_redis()

    await gc_exec._write_state(
        r,
        "wos:gift_code_redeem:state",
        status="running",
        started_at=42.5,
        token=None,
    )

    # ``None`` fields are dropped; remaining values coerced to strings.
    r.hset.assert_awaited_once_with(
        "wos:gift_code_redeem:state",
        mapping={"status": "running", "started_at": "42.5"},
    )
    r.expire.assert_awaited_once_with(
        "wos:gift_code_redeem:state", gc_exec._STATE_TTL_SECONDS
    )


@pytest.mark.asyncio
async def test_write_state_skips_when_all_fields_none() -> None:
    r = _fake_redis()

    await gc_exec._write_state(r, "wos:gift_code_redeem:state", only=None, also=None)

    r.hset.assert_not_awaited()
    r.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_state_noop_without_redis() -> None:
    await gc_exec._write_state(None, "ignored", status="x")  # no exception


# ── scrape handler ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_notifies_when_new_codes_found(mocker: MockerFixture) -> None:
    spec = _wos_spec()
    mocker.patch.object(spec, "poll_once", AsyncMock(return_value=["CODE_A", "CODE_B"]))
    notify = mocker.patch.object(gc_exec, "push_ui_notification", new=AsyncMock())

    handler = gc_exec.DSL_EXEC_HANDLERS["gift_code_scrape"]
    await handler(_ctx(_fake_redis()))

    notify.assert_awaited_once()
    kwargs = notify.await_args.kwargs
    assert kwargs["kind"] == "exec.gift_code_scrape"
    assert kwargs["level"] == "info"
    assert kwargs["payload"] == {"codes": ["CODE_A", "CODE_B"]}


@pytest.mark.asyncio
async def test_scrape_silent_when_no_new_codes(mocker: MockerFixture) -> None:
    spec = _wos_spec()
    mocker.patch.object(spec, "poll_once", AsyncMock(return_value=[]))
    notify = mocker.patch.object(gc_exec, "push_ui_notification", new=AsyncMock())

    handler = gc_exec.DSL_EXEC_HANDLERS["gift_code_scrape"]
    await handler(_ctx(_fake_redis()))

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_scrape_swallows_scraper_errors(mocker: MockerFixture) -> None:
    spec = _wos_spec()
    mocker.patch.object(
        spec, "poll_once", AsyncMock(side_effect=RuntimeError("net down"))
    )
    notify = mocker.patch.object(gc_exec, "push_ui_notification", new=AsyncMock())

    # Must not raise — scraping is best-effort during a DSL tick.
    handler = gc_exec.DSL_EXEC_HANDLERS["gift_code_scrape"]
    await handler(_ctx(_fake_redis()))

    notify.assert_not_awaited()


# ── redeem handler ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redeem_notifies_and_skips_when_lock_already_held(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(gc_exec, "_acquire_lock", new=AsyncMock(return_value=False))
    notify = mocker.patch.object(gc_exec, "push_ui_notification", new=AsyncMock())
    create_task = mocker.patch.object(gc_exec.asyncio, "create_task")

    handler = gc_exec.DSL_EXEC_HANDLERS["gift_code_redeem"]
    await handler(_ctx(_fake_redis()))

    create_task.assert_not_called()
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["kind"] == "exec.gift_code_redeem.already_running"


@pytest.mark.asyncio
async def test_redeem_starts_background_task_when_lock_acquired(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(gc_exec, "_acquire_lock", new=AsyncMock(return_value=True))
    mocker.patch.object(gc_exec, "_write_state", new=AsyncMock())
    background = mocker.patch.object(gc_exec, "_run_redeem_background", new=AsyncMock())

    bg_set: set[asyncio.Task[None]] = set()
    mocker.patch.object(gc_exec, "_BACKGROUND_GIFT_REDEEM_TASKS", bg_set)

    handler = gc_exec.DSL_EXEC_HANDLERS["gift_code_redeem"]
    await handler(_ctx(_fake_redis()))

    assert len(bg_set) == 1
    task = next(iter(bg_set))
    await task  # let the mocked background coroutine complete
    background.assert_awaited_once()
