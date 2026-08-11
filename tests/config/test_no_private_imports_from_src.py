"""A module under ``games/`` must not import an underscore name out of ``src/``.

Forty-four imports across twenty-two game modules reached for names like
``tasks.dsl_exec.context._resolve_player_id_for_device_level_exec``. The
underscore said "internal, may change freely" while the import count said the
opposite, and every consumer had to spell out that contradiction.

It is not only a naming complaint. A private name is not part of any surface a
test can hold still: ``games/wos/intel/tests/test_chain.py`` patched
``tasks.dsl_scenario_helpers._enqueue_scenario`` on the module, so the moment
the callers bound the public name instead, five tests stopped intercepting
anything and started hitting the real queue helper. Two names for one function
is what let that happen quietly.

Scoped to first-party ``src/`` packages: a third-party underscore is out of our
hands, and a module's own private helpers are exactly what privacy is for.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAMES = _REPO_ROOT / "games"

# Top-level packages under src/. Read from disk rather than listed by hand so a
# new package is covered the day it appears.
_SRC_PACKAGES = frozenset(
    p.name for p in (_REPO_ROOT / "src").iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
)


def _module_of(path: Path) -> str:
    """``games/wos/core/arena/exec.py`` → ``games.wos.core.arena.exec``."""
    return path.relative_to(_REPO_ROOT).with_suffix("").as_posix().replace("/", ".")


def _private_imports(path: Path) -> list[str]:
    """Private names this file imports from *another* module.

    Both directions count: out of ``src/``, and across ``games/`` — the same
    defect wearing a different prefix. ``arena/exec.py`` reached into
    ``main_menu.exec._scan_panel_rows`` and ``war_academy/exec.py`` into
    ``research_center.exec._capture``; each one is a module API that was never
    declared as one, so main_menu could not tell it had callers.

    A file importing a private name from its own package is fine — that is a
    module split, not a leak — so a shared prefix is not reported. A module's
    own ``tests/`` counts as inside it: white-box testing of a private helper is
    the point of keeping it private and local.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover — a broken file is another test's problem
        return []

    own_package = _module_of(path).rsplit(".", 1)[0].removesuffix(".tests")
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        root = node.module.split(".")[0]
        if root not in _SRC_PACKAGES and root != "games":
            continue
        if node.module == own_package or node.module.startswith(f"{own_package}."):
            continue
        found += [f"{node.module}.{a.name}" for a in node.names if a.name.startswith("_")]
    return found


def test_no_game_module_imports_a_private_name_from_src() -> None:
    offenders = {
        path.relative_to(_REPO_ROOT).as_posix(): names
        for path in sorted(_GAMES.rglob("*.py"))
        if (names := _private_imports(path))
    }

    assert not offenders, (
        "these game modules import underscore-prefixed names from src/. If the name "
        "is genuinely shared, publish it (drop the underscore at the definition and "
        "update every caller — do NOT leave an alias behind, it splits monkeypatching):\n"
        + "\n".join(f"  {f}: {sorted(n)}" for f, n in sorted(offenders.items()))
    )


def test_the_published_helpers_are_importable_under_their_public_names() -> None:
    """The concrete set this test was written for — a rename that missed one of
    these would otherwise only surface as an ImportError at scenario runtime."""
    from navigation.hero_grid_search import load_hero_template_gray
    from tasks.dsl_exec.context import decode_redis_raw, resolve_player_id_for_device_level_exec
    from tasks.dsl_exec.dismiss_popup import popup_tap_target
    from tasks.dsl_scenario_helpers import (
        enqueue_scenario,
        parse_hms_to_seconds,
        resolve_push_expires_at,
    )

    for fn in (
        decode_redis_raw,
        resolve_player_id_for_device_level_exec,
        enqueue_scenario,
        parse_hms_to_seconds,
        resolve_push_expires_at,
        load_hero_template_gray,
        popup_tap_target,
    ):
        assert callable(fn)


def test_no_underscore_alias_survives_for_them() -> None:
    """An alias would let one caller patch ``_enqueue_scenario`` while another
    binds ``enqueue_scenario``, which is the exact five-test failure above."""
    import navigation.hero_grid_search as hero_grid
    import tasks.dsl_exec.context as context
    import tasks.dsl_exec.dismiss_popup as dismiss_popup
    import tasks.dsl_scenario_helpers as helpers

    stale = {
        f"{mod.__name__}.{name}"
        for mod, names in (
            (context, ("_decode_redis_raw", "_resolve_player_id_for_device_level_exec")),
            (helpers, ("_enqueue_scenario", "_parse_hms_to_seconds", "_resolve_push_expires_at")),
            (hero_grid, ("_load_hero_template_gray",)),
            (dismiss_popup, ("_popup_tap_target",)),
        )
        for name in names
        if hasattr(mod, name)
    }

    assert not stale, f"underscore aliases still exported: {sorted(stale)}"
