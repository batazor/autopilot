from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
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
    from pytest_mock import MockerFixture


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session", autouse=True)
def _session_settings(settings: Settings) -> Iterator[None]:
    set_settings(settings)
    yield
    reset_settings()


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

    c = RedisContainer("redis:7-alpine")
    try:
        c.start()
    except Exception as e:
        pytest.skip(f"Testcontainers Redis unavailable (Docker?): {e!s}")
    try:
        yield c
    finally:
        with contextlib.suppress(Exception):
            c.stop()


@pytest.fixture()
async def redis_async(redis_container: RedisContainer) -> AsyncIterator[aioredis.Redis]:
    """Async redis client flushed per test."""
    url = _redis_url_from_container(redis_container)
    r = aioredis.from_url(url, decode_responses=True)
    try:
        await r.flushdb()
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
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


@pytest.fixture()
def pin_click_to_center(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable random-in-bbox click jitter so tests can pin exact pixel coords.

    Production code samples a random point inside the region/template bbox; that
    is intentional (varies per-click to look human-like). Tests that pre-date the
    randomisation pin the bbox centre, so they opt into this fixture instead of
    encoding tolerance ranges.
    """
    from layout import bbox_percent as _bp
    from navigation import navigator as _nav
    from tasks import dsl_scenario_inline_mixin as _inline

    monkeypatch.setattr(
        _inline,
        "bbox_percent_random_point_to_device_point",
        _bp.bbox_percent_center_to_device_point,
    )
    monkeypatch.setattr(
        _nav,
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
