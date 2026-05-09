from __future__ import annotations

from typing import Any

import pytest

import tasks.dsl_exec as dsl_exec


class _FakeActions:
    def capture_screen_bgr(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def tap(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_exec_detect_screen_persists_current_screen(
    monkeypatch: Any,
    redis_async: object,
) -> None:
    redis = redis_async

    class _FakeNavigator:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def detect_current_screen(self, instance_id: str) -> str:
            assert instance_id == "bs1"
            await redis.hset("wos:instance:bs1:state", "current_screen", "main_city")
            return "main_city"

    monkeypatch.setattr(dsl_exec, "BotActions", _FakeActions)
    monkeypatch.setattr(dsl_exec, "Navigator", _FakeNavigator)

    ctx = dsl_exec.DslExecContext(
        redis_client=redis,
        player_id="",
        instance_id="bs1",
    )

    await dsl_exec.DSL_EXEC_REGISTRY["detect_screen"](ctx)

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur == "main_city"
