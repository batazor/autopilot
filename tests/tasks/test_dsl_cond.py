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


def test_cond_eq_none_matches_empty_or_unknown_tokens() -> None:
    assert dsl._eval_simple_screen_cond("currentNode == none", "") is True
    assert dsl._eval_simple_screen_cond("current_screen == none", "none") is True
    assert dsl._eval_simple_screen_cond("current_screen == unknown", "") is True


def test_cond_eq_none_false_when_screen_known() -> None:
    assert dsl._eval_simple_screen_cond("currentNode == none", "main_city") is False


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
    await r.hset("wos:instance:bs1:state", mapping={"current_screen": "main_city"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    step = {"wait": "1s", "cond": "currentNode != main_city"}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_proceeds_when_async_redis_screen_differs(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"current_screen": "chief_profile"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    step = {"wait": "1s", "cond": "currentNode != main_city"}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_instance_text_substring_shelter_matches_ocr_noise(redis_async: object) -> None:
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"chapter.task": "ade2Bunk Beds in Shelter 2 to Lv. 4 1D ) 2)"},
    )
    step = {"push_scenario": {"name": "building.upgrade"}, "cond": 'chapter.task ~= "Shelter"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_instance_text_substring_false_is_valid_syntax(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Build something else"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    step = {"push_scenario": {"name": "building.upgrade"}, "cond": 'chapter.task ~= "Shelter"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_instance_text_substring_pipe_matches_any_alternative(redis_async: object) -> None:
    r = redis_async
    for text in ("Upgrade Wall", "Build Barracks", "ade2Upgrade x"):
        await r.hset("wos:instance:bs1:state", mapping={"chapter.task": text})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        step = {"push_scenario": {"name": "building.upgrade"}, "cond": 'chapter.task ~= "Upgrade|Build"'}
        allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
        assert allowed is True, text


@pytest.mark.asyncio
async def test_cond_instance_text_substring_pipe_all_miss(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Train troops"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    step = {"push_scenario": {"name": "building.upgrade"}, "cond": 'chapter.task ~= "Upgrade|Build"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_instance_text_rhs_strips_unicode_smart_quotes(redis_async: object) -> None:
    r = redis_async
    await r.hset("wos:instance:bs1:state", mapping={"chapter.task": "Bunk Beds in Shelter 2"})  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    step = {
        "push_scenario": {"name": "building.upgrade"},
        "cond": "chapter.task ~= \u201cShelter\u201d",
    }
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_text_reads_player_state_for_ocr_default_scope(redis_async: object) -> None:
    """OCR ``store:`` writes player-scoped by default — text-cond must read from there.

    Regression: ``squad_fight`` scenario's loop ``cond: squad_status ~= "victory|defeat"``
    used to always evaluate False because the cond reader only looked at instance state,
    while OCR had written ``squad_status`` to ``wos:player:<pid>:state``.
    """
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", mapping={"active_player": "765502864"}
    )
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:765502864:state", mapping={"squad_status": "Victory!"}
    )
    step = {"cond": 'squad_status ~= "victory|defeat"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_text_falls_back_to_instance_state_when_player_field_missing(
    redis_async: object,
) -> None:
    """Existing instance-scoped fields (``chapter.task`` written elsewhere, DSL bookkeeping)
    must still resolve when the player hash doesn't carry the field.
    """
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "765502864", "chapter.task": "Bunk Beds in Shelter 2"},
    )
    # Player hash deliberately empty — fallback path must engage.
    step = {"cond": 'chapter.task ~= "Shelter"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_text_player_state_wins_over_instance_state(redis_async: object) -> None:
    """When both scopes have the field, player state wins (OCR-store default scope)."""
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "765502864", "squad_status": "stale"},
    )
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:player:765502864:state", mapping={"squad_status": "Victory!"}
    )
    step = {"cond": 'squad_status ~= "victory"'}
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", r)  # type: ignore[arg-type]
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_arithmetic_against_player_state_truthy(redis_async: object) -> None:
    """Non-regex cond falls back to ``eval_cond`` against the supplied player state."""
    state_flat = {
        "exploration.state.myPower": 1000,
        "exploration.state.enemyPower": 800,
    }
    step = {
        "push_scenario": {"name": "x"},
        "cond": "exploration.state.myPower * 1.2 >= exploration.state.enemyPower",
    }
    # 1000 * 1.2 = 1200 >= 800 → True.
    allowed = await dsl._dsl_cond_allows_step(  # type: ignore[arg-type]
        step, "bs1", redis_async, state_flat=state_flat
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_cond_arithmetic_against_player_state_falsy(redis_async: object) -> None:
    state_flat = {
        "exploration.state.myPower": 100,
        "exploration.state.enemyPower": 999,
    }
    step = {
        "push_scenario": {"name": "x"},
        "cond": "exploration.state.myPower * 1.2 >= exploration.state.enemyPower",
    }
    # 100 * 1.2 = 120 >= 999 → False.
    allowed = await dsl._dsl_cond_allows_step(  # type: ignore[arg-type]
        step, "bs1", redis_async, state_flat=state_flat
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_cond_arithmetic_without_state_flat_skips(redis_async: object) -> None:
    """Without ``state_flat`` the arithmetic cond can't be evaluated → step skipped."""
    step = {
        "push_scenario": {"name": "x"},
        "cond": "exploration.state.myPower * 1.2 >= exploration.state.enemyPower",
    }
    allowed = await dsl._dsl_cond_allows_step(step, "bs1", redis_async)  # type: ignore[arg-type]
    assert allowed is False


# ---------------------------------------------------------------------------
# ``cond: <field> != null`` — the canonical "wait for who_i_am to complete"
# gate. Bare ``null`` / ``none`` / ``nil`` / ``empty`` normalise to the empty
# string on the right-hand side; quoted forms keep their literal meaning.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cond_field_ne_null_true_when_field_is_set(redis_async: object) -> None:
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "765502864"},
    )
    step = {"cond": "active_player != null"}
    assert await dsl._dsl_cond_allows_step(step, "bs1", r) is True  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cond_field_ne_null_false_when_field_is_unset(redis_async: object) -> None:
    """The ``who_i_am`` gate: every player-bound scenario can use this to
    short-circuit until identity is resolved."""
    step = {"cond": "active_player != null"}
    assert await dsl._dsl_cond_allows_step(step, "bs1", redis_async) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["null", "none", "nil", "empty", "NULL", "None"])
async def test_cond_empty_tokens_all_normalise_to_empty(
    redis_async: object, token: str
) -> None:
    """All four bare tokens (case-insensitive) treat the field as empty."""
    step = {"cond": f"active_player != {token}"}
    # Field unset → ``!=`` against empty → False (gate engaged).
    assert await dsl._dsl_cond_allows_step(step, "bs1", redis_async) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cond_quoted_null_preserves_literal_meaning(redis_async: object) -> None:
    """``!= "null"`` (with quotes) is a literal string compare — the field
    value happens to be the four-character word ``"null"``, not an empty cell.
    Used rarely but the escape hatch must work."""
    r = redis_async
    await r.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "null"},
    )
    step = {"cond": 'active_player != "null"'}
    # Literal compare: "null" != "null" → False.
    assert await dsl._dsl_cond_allows_step(step, "bs1", r) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cond_field_eq_null_true_when_field_is_unset(redis_async: object) -> None:
    """The inverse gate: skip a step unless the field is empty — useful for
    things like ``startup`` scenarios that should only run pre-identity."""
    step = {"cond": "active_player == null"}
    assert await dsl._dsl_cond_allows_step(step, "bs1", redis_async) is True  # type: ignore[arg-type]
