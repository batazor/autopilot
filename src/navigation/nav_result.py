"""Typed outcome of a navigation attempt.

:meth:`Navigator.navigate_to` returned a bare ``bool``. Internally the navigator
distinguishes six or seven reasons a route can fail — no route at all, the route
existed but a tap was rejected, the tap landed but the destination never
verified, an opaque overlay with no back button, retries exhausted — and every
one of them collapsed into ``False`` at the boundary.

The caller then *guessed* the cause back. The DSL gate literally wrote
``"(no route, verify failed after tap, or tap blocked)"`` into ``nav_error``, and
that guess is what an operator read. Two workarounds grew around the missing
information: a timestamp probe comparing ``last_approval_reject_at`` against the
attempt start to recover "operator rejected", and an allowed-node re-check to
recover "verify lost a race".

``NavResult`` carries the cause the navigator already knew.

Note the ``__bool__``: existing call sites do ``if not await navigate_to(...)``
and keep working unchanged, so adopting the type is not a flag-day.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NavFailure(StrEnum):
    """Why a navigation attempt did not end on the target screen."""

    NO_ROUTE = "no_route"
    """BFS found no path from the current screen to the target."""

    NO_ROUTE_TO_HUB = "no_route_to_hub"
    """Off-route and not even a path back to ``main_city`` to restart from."""

    TAP_BLOCKED = "tap_blocked"
    """A hop's tap did not execute — approval rejected it, or the region was gone."""

    VERIFY_FAILED = "verify_failed"
    """The tap executed but the destination screen never verified.

    Previously invisible: ``_execute_hops`` has always distinguished this, and
    no caller ever branched on it, so it surfaced only as retries-exhausted.
    """

    UNKNOWN_SCREEN_NO_BACK = "unknown_screen_no_back"
    """Stuck on an unrecognised screen with no usable back button — typically a
    full-screen ad. The navigator cannot clear it; a popup dismisser must."""

    UNKNOWN_TARGET = "unknown_target"
    """The requested node is not in the screen graph at all — a scenario
    referencing a screen that does not exist. Never reaches the Navigator."""

    RETRIES_EXHAUSTED = "retries_exhausted"
    """Ten attempts without landing on the target and without a sharper cause."""


@dataclass(frozen=True, slots=True)
class NavResult:
    """Outcome of one ``navigate_to`` call."""

    ok: bool
    failure: NavFailure | None = None
    src: str = ""
    """Screen the navigator was on when it gave up (``""`` if never detected)."""
    dst: str = ""
    """Screen it was asked to reach."""
    attempt: int = 0
    """Zero-based attempt index at which it gave up."""
    route_explain: str = ""
    """``screen_graph.format_route_explain`` output, when the failure produced one.

    This used to be written into ``nav_error`` by the navigator and then
    overwritten microseconds later by the DSL gate's guess, so it never reached
    a reader.
    """

    def __bool__(self) -> bool:
        """Truthiness is success, so ``if not result:`` reads naturally and the
        pre-existing bool call sites keep working."""
        return self.ok

    @property
    def reason(self) -> str:
        """Failure name for logs / state fields; ``""`` on success."""
        return "" if self.ok or self.failure is None else str(self.failure)


def nav_ok(*, src: str = "", dst: str = "", attempt: int = 0) -> NavResult:
    """Successful arrival."""
    return NavResult(ok=True, src=src, dst=dst, attempt=attempt)


def nav_failed(
    failure: NavFailure,
    *,
    src: str = "",
    dst: str = "",
    attempt: int = 0,
    route_explain: str = "",
) -> NavResult:
    """Failed attempt carrying its cause."""
    return NavResult(
        ok=False,
        failure=failure,
        src=src,
        dst=dst,
        attempt=attempt,
        route_explain=route_explain,
    )
