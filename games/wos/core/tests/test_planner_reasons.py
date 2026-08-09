"""One verdict vocabulary for every planner and allocator.

Fifteen modules declared these strings independently — `SELECTED` in thirteen of
them, `NONE` in nine, `INSUFFICIENT_RESOURCES` in seven. Same spelling, same
meaning, no place to read the list, and nothing stopping the sixteenth planner
from inventing `no_resources` instead. They are what `botctl why` and the
/planner UI show an operator, so consistency across domains is the point.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from games.wos.core import planner_reasons as R

_REPO_ROOT = Path(__file__).resolve().parents[4]
_GAMES = _REPO_ROOT / "games"

_SHARED_NAMES = frozenset(
    n for n in dir(R) if n.isupper() and isinstance(getattr(R, n), str)
)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SELECTED", "selected"),
        ("NONE", "none"),
        ("LOCKED", "locked"),
        ("INSUFFICIENT_RESOURCES", "insufficient_resources"),
        ("INSUFFICIENT_STAMINA", "insufficient_stamina"),
        ("ALL_MAXED", "all_maxed"),
        ("WINDOW_CLOSED", "window_closed"),
        ("QUOTA_FULL", "quota_full"),
        ("RESERVE_HELD", "reserve_held"),
        ("NOT_CONSIDERED", "not_considered"),
        ("CONSUME", "consume"),
        ("SUPPLY", "supply"),
        ("IDLE", "idle"),
    ],
)
def test_values_are_frozen(name: str, value: str) -> None:
    """They are compared as plain strings, ride in API payloads and decision
    traces, and are asserted verbatim by domain tests. Changing one is a
    behaviour change, not a rename."""
    assert getattr(R, name) == value


def test_no_module_redeclares_a_shared_reason() -> None:
    """The regression this replaces: a domain quietly defining its own copy.

    A second declaration is not harmless — it is how two domains end up
    reporting `no_resources` and `insufficient_resources` for the same thing.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(_GAMES.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if "/tests/" in rel or path.name == "planner_reasons.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in _SHARED_NAMES
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                offenders.setdefault(rel, []).append(node.targets[0].id)

    assert not offenders, (
        "these modules redeclare a shared planner reason instead of importing "
        "it from games.wos.core.planner_reasons:\n"
        + "\n".join(f"  {f}: {sorted(n)}" for f, n in sorted(offenders.items()))
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "games.wos.core.building.planner.planner",
        "games.wos.core.charms.planner.planner",
        "games.wos.core.gear.planner.planner",
        "games.wos.core.hero_gear.planner.planner",
        "games.wos.core.island.planner.planner",
        "games.wos.core.pets.planner.planner",
        "games.wos.core.research.planner.planner",
        "games.wos.core.resources.allocator",
        "games.wos.core.stamina.allocator",
        "games.wos.core.vip.planner.planner",
        "games.wos.heroes.heroes.planner.planner",
        "games.wos.intel.planner.planner",
        "games.wos.troops.planner.planner",
    ],
)
def test_each_domain_still_exposes_the_names_it_used_to(module_name: str) -> None:
    """Domains re-export the shared names, so existing `planner.SELECTED`
    references keep resolving to the same string."""
    module = importlib.import_module(module_name)

    exported = {n: getattr(module, n) for n in dir(module) if n in _SHARED_NAMES}

    assert exported, f"{module_name} exposes no shared reason at all"
    for name, value in exported.items():
        assert value == getattr(R, name)


def test_the_registry_set_matches_the_module() -> None:
    """`PLANNER_REASONS` is used for membership checks; a name missing from it
    would read as an unknown verdict."""
    assert {getattr(R, n) for n in _SHARED_NAMES if n != "PLANNER_REASONS"} == R.PLANNER_REASONS
