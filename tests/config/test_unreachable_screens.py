"""Startup check: a detectable screen the navigator cannot route TO.

``_validate_dead_end_screens`` already covers the outbound direction ("cannot
leave this screen"). The inbound direction was unchecked, which is how ``arena``
ended up detectable, exitable, and reachable only through a hand-rolled gesture
``exec:`` — see the validator's docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from config.startup_validation import _validate_unreachable_screens

if TYPE_CHECKING:
    from pathlib import Path


def _findings(issues: list[Any]) -> dict[str, Any]:
    return {i.source: i for i in issues if "NO declared" in i.message}


def _patch_graph(
    mocker: Any, adjacency: dict[str, set[str]], screens: dict[str, tuple[int, bool, str]]
) -> None:
    """Pin one catalog with a known graph and a known screen set.

    The check resolves screen names PER CATALOG — a name only means something
    inside the catalog that declares it — so the per-catalog collector is what
    has to be stubbed here, not the cross-catalog union.
    """
    import navigation.screen_graph as sg

    mocker.patch.object(sg, "graph_for_game", lambda _g=None: ({}, {}, adjacency))
    mocker.patch(
        "config.startup_validation._collect_screen_verify_entries_for_catalog",
        return_value=screens,
    )
    mocker.patch("config.games.iter_games", return_value=("wos",))


def test_screen_with_no_inbound_edge_is_reported(
    mocker: Any, tmp_path: Path
) -> None:
    _patch_graph(
        mocker,
        # `arena` can be left but never entered — exactly the live shape.
        {"arena": {"main_city"}, "arena.challenge_list": {"arena"}},
        {"arena": (30, False, "games/wos/core/arena/routes/screen_verify.yaml")},
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    found = _findings(issues)
    assert "screen_verify:arena" in found
    assert found["screen_verify:arena"].severity == "warning"


def test_reachable_screen_is_not_reported(mocker: Any, tmp_path: Path) -> None:
    _patch_graph(
        mocker,
        {"main_city": {"main_world"}, "main_world": {"intel"}, "intel": {"main_world"}},
        {"intel": (30, False, "games/wos/intel/routes/screen_verify.yaml")},
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    assert _findings(issues) == {}


def test_low_priority_modal_is_exempt(mocker: Any, tmp_path: Path) -> None:
    """Popups are dismissed by overlay scenarios, not routed to.

    The cutoff is ``MAIN_CITY_HUB_PRIORITY`` (10), so the fixture priority has to
    be below it — 20 is already a routable screen.
    """
    _patch_graph(
        mocker,
        {"main_city": {"main_world"}},
        {"some.popup": (5, False, "x/screen_verify.yaml")},
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    assert _findings(issues) == {}


def test_terminal_flag_does_not_exempt(mocker: Any, tmp_path: Path) -> None:
    """``terminal: true`` says its own scenario taps OUT — that says nothing
    about getting in, so it must not silence this check."""
    _patch_graph(
        mocker,
        {"main_city": {"main_world"}},
        {"stranded": (30, True, "x/screen_verify.yaml")},
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    assert "screen_verify:stranded" in _findings(issues)


def test_entry_scenario_annotation_opts_out(mocker: Any, tmp_path: Path) -> None:
    _patch_graph(
        mocker,
        {"main_city": {"main_world"}},
        {"battle.result": (30, False, "x/screen_verify.yaml")},
    )
    mocker.patch(
        "config.startup_validation._screen_verify_entry_opts_out_of_reachability",
        return_value=True,
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    assert _findings(issues) == {}


def test_synthesized_building_edges_count_as_inbound(
    mocker: Any, tmp_path: Path
) -> None:
    """Per-building edges exist only in the BUILT graph, never in edge_taps.yaml.
    Reading the YAML directly reported every building screen as a false positive.
    """
    _patch_graph(
        mocker,
        {"main_city": {"sawmill"}, "sawmill": {"main_city"}},
        {"sawmill": (31, False, "x/screen_verify.yaml")},
    )
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    assert _findings(issues) == {}


@pytest.mark.parametrize("adjacency", [{}, {"main_city": set()}])
def test_empty_graph_reports_nothing(
    mocker: Any, tmp_path: Path, adjacency: dict[str, set[str]]
) -> None:
    """A graph that failed to build must not flood the boot with false positives."""
    _patch_graph(mocker, adjacency, {"arena": (30, False, "x/screen_verify.yaml")})
    issues: list[Any] = []

    _validate_unreachable_screens(tmp_path, issues)

    if not adjacency:
        assert _findings(issues) == {}
