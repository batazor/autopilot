from __future__ import annotations

import pytest
import tasks.dsl_scenario as dsl

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
async def test_cond_skips_when_async_redis_returns_bytes_main_city(redis_async: object) -> None:
    """Regression: ``redis.asyncio.from_url`` returns bytes by default; the cond
    check used to wrap them with ``str()`` and compare ``"b'main_city'"`` against
    ``"main_city"``, so steps with ``cond: currentNode != main_city`` always ran.
    """

    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"current_screen": "main_city"})  # type: ignore[attr-defined]
    step = {"set_node": "main_city", "cond": "currentNode != main_city"}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_proceeds_when_async_redis_screen_differs(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"current_screen": "chief_profile"})  # type: ignore[attr-defined]
    step = {"set_node": "main_city", "cond": "currentNode != main_city"}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_instance_text_substring_shelter_matches_ocr_noise(redis_async: object) -> None:
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"chapter.task": "ade2Bunk Beds in Shelter 2 to Lv. 4 1D ) 2)"},
    )
    step = {"push_scenario": {"name": "upgrade"}, "cond": 'chapter.task ~= "Shelter"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_instance_text_substring_false_is_valid_syntax(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Build something else"})  # type: ignore[attr-defined]
    step = {"push_scenario": {"name": "upgrade"}, "cond": 'chapter.task ~= "Shelter"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_instance_text_substring_pipe_matches_any_alternative(redis_async: object) -> None:
    r = redis_async
    for text in ("Upgrade Wall", "Build Barracks", "ade2Upgrade x"):
        await r.hset("wos:instance:bs1:state", mapping={"chapter.task": text})  # type: ignore[attr-defined]
        step = {"push_scenario": {"name": "upgrade"}, "cond": 'chapter.task ~= "Upgrade|Build"'}
        allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
        assert allowed is True, text


@pytest.mark.asyncio
async def test_cond_instance_text_substring_pipe_all_miss(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Train troops"})  # type: ignore[attr-defined]
    step = {"push_scenario": {"name": "upgrade"}, "cond": 'chapter.task ~= "Upgrade|Build"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_instance_text_rhs_strips_unicode_smart_quotes(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Bunk Beds in Shelter 2"})  # type: ignore[attr-defined]
    step = {
        "push_scenario": {"name": "upgrade"},
        "cond": "chapter.task ~= \u201cShelter\u201d",
    }
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True
