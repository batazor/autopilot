from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import numpy as np
import pytest
import redis
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer

from adb import BotActions
from config.loader import Settings, load_settings, reset_settings, set_settings
from ocr.client import OcrClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session", autouse=True)
def _session_settings(settings: Settings) -> Iterator[None]:
    set_settings(settings)
    yield
    reset_settings()


@pytest.fixture(autouse=True)
def _disable_api_startup_gift_code_scrape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep API TestClient lifespans from scraping public gift-code sources."""
    monkeypatch.setenv("WOS_GIFT_CODES_STARTUP_SCRAPE", "0")


@pytest.fixture(autouse=True)
def _isolate_state_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the SQLite state DB at a throwaway file for every test.

    Without this, any test that exercises a code path which writes the device /
    player registry (e.g. ``who_i_am`` OCR → ``set_last_active_player``,
    ``fetch_player`` → ``upsert_device_gamer``) persists its fixture data into
    the real ``db/state/state.db`` — which is how a test player id like
    ``player_42`` leaked into the live registry. Tests that manage the path
    themselves (the ``sqlite_db`` fixtures in config tests) set their own
    override *after* this one and reset it on teardown, so they're unaffected.
    """
    from config.state_sqlite import set_state_db_path_for_tests

    db_path = tmp_path_factory.mktemp("state-db") / "state.db"
    set_state_db_path_for_tests(db_path)
    try:
        yield
    finally:
        set_state_db_path_for_tests(None)


@pytest.fixture(scope="session")
def ocr_client(settings: Settings) -> OcrClient:
    return OcrClient(settings)


def _redis_url_from_container(c: RedisContainer) -> str:
    host = c.get_container_host_ip()
    port = int(c.get_exposed_port(6379))
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    """Session-scoped Redis container for integration tests.

    Skips when Docker/testcontainers is unavailable or ``WOS_TESTCONTAINERS=0``.
    """
    if os.environ.get("WOS_TESTCONTAINERS", "").strip() in {"0", "false", "no"}:
        pytest.skip("Testcontainers disabled via WOS_TESTCONTAINERS=0")

    # Construct *inside* the guard: a broken daemon (e.g. Docker returning a 500
    # on the version handshake) raises during client init here, not just on
    # ``start()``. Keeping construction outside would let that escape the skip
    # and error every dependent test instead of skipping it.
    try:
        c = RedisContainer("redis:7-alpine")
        c.start()
    except Exception as e:
        pytest.skip(f"Testcontainers Redis unavailable (Docker?): {e!s}")
    try:
        yield c
    finally:
        with contextlib.suppress(Exception):
            c.stop()


@pytest.fixture
async def redis_async(redis_container: RedisContainer) -> AsyncIterator[aioredis.Redis]:
    """Async redis client flushed per test."""
    url = _redis_url_from_container(redis_container)
    r = aioredis.from_url(url, decode_responses=True)
    try:
        await r.flushdb()
        yield r
    finally:
        await r.aclose()


@pytest.fixture
def redis_sync(redis_container: RedisContainer) -> Iterator[redis.Redis]:
    """Sync redis client flushed per test (for code using redis.Redis, not asyncio)."""
    url = _redis_url_from_container(redis_container)
    r = redis.Redis.from_url(url, decode_responses=True)
    r.flushdb()
    try:
        yield r
    finally:
        with contextlib.suppress(Exception):
            r.close()


@pytest.fixture
def pin_click_to_center(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable random-in-bbox click jitter so tests can pin exact pixel coords.

    Production code samples a random point inside the region/template bbox; that
    is intentional (varies per-click to look human-like). Tests that pre-date the
    randomisation pin the bbox centre, so they opt into this fixture instead of
    encoding tolerance ranges.
    """
    from layout import bbox_percent as _bp
    from navigation import tap_executor as _tap_exec
    from tasks import dsl_scenario_inline_mixin as _inline

    monkeypatch.setattr(
        _inline,
        "bbox_percent_random_point_to_device_point",
        _bp.bbox_percent_center_to_device_point,
    )
    # Navigator's region tap now lives in tap_executor; patch it where it's used.
    monkeypatch.setattr(
        _tap_exec,
        "bbox_percent_random_point_to_device_point",
        _bp.bbox_percent_center_to_device_point,
    )


def make_actions(
    frames: list[np.ndarray] | np.ndarray | None = None,
    *,
    resolution: tuple[int, int] | None = None,
) -> MagicMock:
    """``MagicMock(spec=BotActions)`` with optional sequential frame capture."""
    if isinstance(frames, np.ndarray):
        frame_list = [frames]
    elif frames is None:
        frame_list = None
    else:
        frame_list = frames

    actions = MagicMock(spec=BotActions)
    if resolution is not None:
        actions.screen_resolution.return_value = resolution
    elif frame_list:
        height, width = frame_list[0].shape[:2]
        actions.screen_resolution.return_value = (int(width), int(height))
    else:
        actions.screen_resolution.return_value = (720, 1280)

    if frame_list is not None:
        it = iter(frame_list)
        last = [frame_list[-1]]

        def next_frame(*_args: object, **_kwargs: object) -> np.ndarray:
            last[0] = next(it, last[0])
            return last[0]

        actions.capture_screen_bgr.side_effect = next_frame
        actions.capture_screen_bgr_cached.side_effect = next_frame
    else:
        blank = np.zeros((1280, 720, 3), dtype=np.uint8)
        actions.capture_screen_bgr.return_value = blank
        actions.capture_screen_bgr_cached.return_value = blank

    actions.tap.return_value = True
    return actions


def patch_dsl(
    mocker: MockerFixture,
    actions: MagicMock,
    *,
    repo_root: Path | str | None = None,
) -> None:
    """Route DSL ``execute()`` to a test double instead of ADB / ``frame_bus``."""
    import tasks.dsl_scenario as dsl
    from tasks import dsl_runtime

    if repo_root is not None:
        mocker.patch.object(dsl, "_repo_root", return_value=repo_root)
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(dsl, "BotActions", return_value=actions)


@pytest.fixture
def make_module_tree(tmp_path: Path) -> Callable[..., Path]:
    """Build a fake module tree at the location ``modules_root_for`` resolves to.

    Use this instead of hardcoding ``tmp_path / "modules" / "core" / ...`` so the
    test transparently follows Phase 3's directory move from ``modules/`` to
    ``games/<game>/`` — the helper resolves the right path for the migration
    phase the code currently sits in.

    Example::

        mod_dir = make_module_tree("core/test_scenarios")
        (mod_dir / "scenarios" / "foo.yaml").write_text(...)
    """
    from config.games import modules_root_for as _modules_root_for

    def _make(module_id: str = "core/test_scenarios", game: str = "wos") -> Path:
        root = _modules_root_for(game, repo_root=tmp_path)
        mod = root / module_id
        mod.mkdir(parents=True, exist_ok=True)
        return mod

    return _make


_INTEGRATION_TIMEOUT_S = 180


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Longer per-test limit for integration tests (Redis, OCR backend, etc.)."""
    for item in items:
        if not item.get_closest_marker("integration"):
            continue
        if item.get_closest_marker("timeout"):
            continue
        item.add_marker(pytest.mark.timeout(_INTEGRATION_TIMEOUT_S))
