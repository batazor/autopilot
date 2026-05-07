from __future__ import annotations

import asyncio

import tasks.dsl_scenario as dsl


def test_cond_ne_screen_passes_when_differs() -> None:
    assert dsl._eval_simple_screen_cond("currentNode != main_city", "chief_profile") is True
    assert dsl._eval_simple_screen_cond("current_screen != main_city", "") is True


def test_cond_ne_screen_fails_when_same() -> None:
    assert dsl._eval_simple_screen_cond("currentNode != main_city", "main_city") is False


def test_cond_eq_screen() -> None:
    assert dsl._eval_simple_screen_cond("current_screen == main_city", "main_city") is True
    assert dsl._eval_simple_screen_cond("current_screen == main_city", "x") is False


def test_cond_unknown_lhs() -> None:
    assert dsl._eval_simple_screen_cond("foo != bar", "") is False


def test_cond_bad_syntax() -> None:
    assert dsl._eval_simple_screen_cond("nonsense", "main_city") is False


def test_decode_redis_value_handles_bytes_str_and_none() -> None:
    assert dsl._decode_redis_value(b"main_city") == "main_city"
    assert dsl._decode_redis_value(b"  spaced  ") == "spaced"
    assert dsl._decode_redis_value("main_city") == "main_city"
    assert dsl._decode_redis_value(None) == ""


class _FakeAsyncRedis:
    """Minimal async stand-in for ``redis.asyncio`` returning ``bytes``."""

    def __init__(self, screen: bytes | str | None) -> None:
        self._screen = screen

    async def hget(self, key: str, field: str) -> bytes | str | None:
        del key, field
        return self._screen


def test_cond_skips_when_async_redis_returns_bytes_main_city() -> None:
    """Regression: ``redis.asyncio.from_url`` returns bytes by default; the cond
    check used to wrap them with ``str()`` and compare ``"b'main_city'"`` against
    ``"main_city"``, so steps with ``cond: currentNode != main_city`` always ran.
    """

    fake = _FakeAsyncRedis(b"main_city")
    step = {"set_node": "main_city", "cond": "currentNode != main_city"}
    allowed = asyncio.run(dsl._dsl_cond_allows_step(step, "bs1", fake))
    assert allowed is False


def test_cond_proceeds_when_async_redis_screen_differs() -> None:
    fake = _FakeAsyncRedis(b"chief_profile")
    step = {"set_node": "main_city", "cond": "currentNode != main_city"}
    allowed = asyncio.run(dsl._dsl_cond_allows_step(step, "bs1", fake))
    assert allowed is True
