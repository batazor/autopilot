"""A module's ``exec.py`` must be ONE module object, not two.

The registry loaded every exec through ``spec_from_file_location`` under a
synthetic ``wos_module_exec_<id>`` name and never registered it in
``sys.modules``. Any exec that another module also imports normally —
``war_academy/exec.py`` imports ``research_center.exec`` — therefore existed as
two distinct module objects built from the same file, each with its own copy of
the module-level tables and its own ``lru_cache``. Immutable tables made that
survivable; the first warm cache or piece of module state up there would not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest

from config.module_exec_registry import (
    _dotted_name,
    _exports_handlers,
    _import_exec_module,
    load_module_exec_handlers,
)
from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


def test_registry_and_a_normal_import_agree_on_the_module() -> None:
    """The concrete case: `war_academy` imports `research_center.exec`."""
    import games.wos.core.research_center.exec as canonical

    registry = load_module_exec_handlers()
    handler = registry.get("start_planned_research")

    assert handler is not None
    assert handler.__module__ == canonical.__name__


def test_no_handler_comes_from_a_synthetic_module() -> None:
    """A synthetic name is the fallback, not the normal path. If one shows up
    here, an exec became circular and is silently running as a private copy."""
    registry = load_module_exec_handlers()

    synthetic = sorted(
        {
            fn.__module__
            for fn in registry.values()
            if fn.__module__.startswith("wos_module_exec_")
        }
    )

    assert synthetic == []


def test_the_import_time_registry_still_has_every_handler() -> None:
    """The registry is built at `tasks.dsl_exec` import, and that is where the
    arena cycle arises: `arena/exec.py` imports `tasks.dsl_exec.context`, which
    lands back in the loader with arena's own module still mid-execution.

    Whether the canonical import or the private-copy fallback wins depends on
    which module the process imported first, so this asserts what IS guaranteed
    — the handlers are all there and callable. Deduplication is asserted against
    the deterministic explicit call above.
    """
    from tasks.dsl_exec import DSL_EXEC_REGISTRY

    for name in ("arena_pick_and_open", "open_arena_via_city"):
        handler = DSL_EXEC_REGISTRY.get(name)
        assert handler is not None, f"{name} vanished from the registry"
        assert callable(handler)


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("games/wos/core/arena/exec.py", "games.wos.core.arena.exec"),
        ("games/kingshot/mail/exec.py", "games.kingshot.mail.exec"),
    ],
)
def test_dotted_name_maps_a_path_to_its_import_path(rel: str, expected: str) -> None:
    assert _dotted_name(repo_root() / rel, repo_root()) == expected


def test_dotted_name_refuses_paths_it_cannot_name(tmp_path: Path) -> None:
    """Outside the repo, or a directory that is not an identifier → no dotted
    name, so the caller falls back to the file-path load rather than guessing."""
    assert _dotted_name(tmp_path / "elsewhere" / "exec.py", repo_root()) == ""
    assert _dotted_name(repo_root() / "games" / "not-an-id" / "exec.py", repo_root()) == ""


def test_unimportable_file_still_loads_by_path(tmp_path: Path) -> None:
    """The fallback has to work: a file outside the repo has no dotted name."""
    exec_py = tmp_path / "exec.py"
    exec_py.write_text("DSL_EXEC_HANDLERS = {'x': lambda ctx: None}\n", encoding="utf-8")

    mod = _import_exec_module(exec_py, "standalone")

    assert mod is not None
    assert _exports_handlers(mod)


def test_partially_initialised_module_is_not_accepted() -> None:
    """The guard behind the circular-import fallback.

    A module caught mid-execution has not reached the handler table at the
    bottom of the file yet, and accepting it would silently drop its handlers.
    """

    class _MidImport:
        pass

    class _Done:
        DSL_EXEC_HANDLERS: ClassVar[dict] = {"a": lambda _ctx: None}

    class _LegacyName:
        EXEC_HANDLERS: ClassVar[dict] = {"a": lambda _ctx: None}

    assert _exports_handlers(_MidImport()) is False
    assert _exports_handlers(_Done()) is True
    assert _exports_handlers(_LegacyName()) is True
