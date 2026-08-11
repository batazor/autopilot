"""Every planner route reports bad input as 400, not as "planner error" 500.

The error contract used to be a call — ``return _guard(run)`` around a nested
``def run()`` that existed only to be passed to it — so honouring it depended on
the author remembering. Six of the thirty-three routes did not.

None of those six was demonstrably broken: probing them found no live 500 (the
``/state`` pair raises its own 404s, and ``/building/autofill``'s ``level_rank``
is total). The point is not a bug fixed, it is that "did this route opt out"
became a question with an answer — a decorator is checkable where a remembered
call is not, and that check is the first test below.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.routing import Route

from api.routers.planner import guarded, router


def _route_endpoints() -> list[tuple[str, Any]]:
    return [
        (f"{sorted(r.methods or [])} {r.path}", r.endpoint)
        for r in router.routes
        if isinstance(r, Route)
    ]


def test_there_are_routes_to_check() -> None:
    """A broken import would otherwise make every assertion below vacuous."""
    assert len(_route_endpoints()) > 25


def test_every_route_carries_the_guard() -> None:
    unguarded = [
        label
        for label, endpoint in _route_endpoints()
        if getattr(endpoint, "__wrapped__", None) is None
    ]

    assert not unguarded, (
        f"these planner routes are not @guarded, so bad input from them reaches the "
        f"operator as a 500: {unguarded}"
    )


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (ValueError("no such level"), 400),
        (KeyError("furnace"), 400),
        (TypeError("nope"), 400),
        (RuntimeError("planner blew up"), 500),
        (ZeroDivisionError(), 500),
    ],
)
def test_it_splits_bad_input_from_planner_failure(raised: Exception, status: int) -> None:
    @guarded
    def endpoint() -> dict[str, Any]:
        raise raised

    with pytest.raises(HTTPException) as excinfo:
        endpoint()

    assert excinfo.value.status_code == status


def test_an_explicit_http_error_passes_through_untouched() -> None:
    """A route that already decided on 404 must not be relabelled a 500."""

    @guarded
    def endpoint() -> dict[str, Any]:
        raise HTTPException(status_code=404, detail="no such player")

    with pytest.raises(HTTPException) as excinfo:
        endpoint()

    assert (excinfo.value.status_code, excinfo.value.detail) == (404, "no such player")


def test_the_signature_survives_wrapping() -> None:
    """FastAPI builds request validation and the OpenAPI schema from the
    signature; a wrapper that hid it would turn typed bodies into ``**kwargs``
    and silently drop validation."""
    import inspect

    def endpoint(player_id: str, limit: int = 5) -> dict[str, Any]:
        return {}

    wrapped = guarded(endpoint)

    assert inspect.signature(wrapped) == inspect.signature(endpoint)
    assert wrapped.__name__ == "endpoint"


def test_the_return_value_is_passed_straight_through() -> None:
    @guarded
    def endpoint(x: int) -> dict[str, int]:
        return {"x": x}

    assert endpoint(7) == {"x": 7}
