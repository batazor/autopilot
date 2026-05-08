from __future__ import annotations

from typing import Any

import pytest

import tasks.dsl_exec as dsl_exec


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, *args: Any, **kwargs: Any) -> None:
        mapping = kwargs.get("mapping")
        if mapping is None and len(args) >= 2:
            mapping = {str(args[0]): str(args[1])}
        if mapping is None:
            mapping = {}
        self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in dict(mapping).items()})


class _FakeActions:
    def capture_screen_bgr(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def tap(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_exec_detect_screen_persists_current_screen(monkeypatch: Any) -> None:
    redis = _FakeRedis()

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

    assert redis.hashes["wos:instance:bs1:state"]["current_screen"] == "main_city"
