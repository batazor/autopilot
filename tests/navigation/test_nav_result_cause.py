"""`navigate_to` reports WHY it failed, and the cause survives to the operator.

`navigate_to` used to return a bare `bool`. The navigator internally knew six or
seven distinct causes and threw all of them away at the boundary, so the DSL gate
reconstructed a guess — it literally wrote "(no route, verify failed after tap,
or tap blocked)" into `nav_error`, and that guess is what an operator read.
"""

from __future__ import annotations

import pytest

from navigation.nav_result import NavFailure, NavResult, nav_failed, nav_ok


def test_result_is_falsy_on_failure_so_old_call_sites_keep_working() -> None:
    """`__bool__` is the migration lever: `if not await navigate_to(...)` in
    `modules/broadcast/runner.py` keeps working without being touched."""
    assert not nav_failed(NavFailure.NO_ROUTE)
    assert nav_ok()

    ok_result = nav_ok(src="main_city", dst="intel")
    assert bool(ok_result) is True
    assert ok_result.ok is True


def test_success_carries_no_cause() -> None:
    result = nav_ok(src="main_city", dst="intel")

    assert result.failure is None
    assert result.reason == ""


def test_failure_reason_is_a_plain_string() -> None:
    """It lands in a Redis hash field and a log line, so it must not need
    enum-aware serialisation at the boundary."""
    result = nav_failed(NavFailure.VERIFY_FAILED, src="main_world", dst="intel")

    assert result.reason == "verify_failed"
    assert isinstance(result.reason, str)


def test_result_is_immutable() -> None:
    """A cause that a caller can rewrite is not evidence."""
    result = nav_failed(NavFailure.TAP_BLOCKED)

    with pytest.raises(AttributeError):
        result.failure = NavFailure.NO_ROUTE  # type: ignore[misc]


def test_route_explain_rides_along() -> None:
    """`format_route_explain` had no surviving consumer: the navigator wrote it
    into `nav_error` and the DSL gate overwrote it microseconds later."""
    result = nav_failed(
        NavFailure.NO_ROUTE,
        src="deals",
        dst="arena",
        route_explain="route deals -> arena\nselected: unreachable",
    )

    assert "unreachable" in result.route_explain


@pytest.mark.parametrize(
    "failure",
    [
        NavFailure.NO_ROUTE,
        NavFailure.NO_ROUTE_TO_HUB,
        NavFailure.TAP_BLOCKED,
        NavFailure.VERIFY_FAILED,
        NavFailure.UNKNOWN_SCREEN_NO_BACK,
        NavFailure.UNKNOWN_TARGET,
        NavFailure.RETRIES_EXHAUSTED,
    ],
)
def test_every_cause_round_trips_through_its_string(failure: NavFailure) -> None:
    """Causes travel through Redis as text and come back for comparison."""
    result = nav_failed(failure)

    assert NavFailure(result.reason) is failure


def test_verify_failed_is_reachable_as_a_cause() -> None:
    """The literal existed in the navigator all along and NO caller branched on
    it — it only ever surfaced disguised as retries-exhausted."""
    assert NavFailure.VERIFY_FAILED in set(NavFailure)
    assert NavResult(ok=False, failure=NavFailure.VERIFY_FAILED).reason == "verify_failed"
