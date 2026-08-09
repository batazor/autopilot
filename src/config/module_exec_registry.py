"""Load DSL ``exec:`` handlers contributed by feature modules.

Each module may ship ``exec.py`` (or a path declared in ``module.yaml`` as
``exec: <relative-path>``) exporting::

    DSL_EXEC_HANDLERS: dict[str, DslExecHandler]

Handlers are merged into :data:`tasks.dsl_exec.DSL_EXEC_REGISTRY` after the
core registry. Duplicate names log a warning; the later module in sorted
``module_id`` order wins.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from config.module_discovery import iter_module_dirs, module_meta_id
from config.paths import ensure_repo_on_sys_path
from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

DslExecHandler = Callable[[Any], Awaitable[None]]



def _load_module_yaml(module_dir: Path) -> dict[str, object]:
    """One shared parse — this used to be a third copy of the same six lines."""
    from config.module_discovery import load_module_yaml

    return load_module_yaml(module_dir)


def _exports_handlers(mod: object) -> bool:
    """Whether ``mod`` has finished executing far enough to expose its table."""
    return isinstance(
        getattr(mod, "DSL_EXEC_HANDLERS", None) or getattr(mod, "EXEC_HANDLERS", None),
        dict,
    )


def _dotted_name(exec_path: Path, repo_root: Path) -> str:
    """Canonical import path for ``exec_path``, or ``""`` if it is outside the repo.

    ``games/wos/core/arena/exec.py`` → ``games.wos.core.arena.exec``. The tree
    uses implicit namespace packages, so no ``__init__.py`` is required for this
    to resolve.
    """
    try:
        rel = exec_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return ""
    parts = rel.with_suffix("").parts
    if not parts or any(not part.isidentifier() for part in parts):
        return ""
    return ".".join(parts)


def _import_exec_module(
    exec_path: Path, module_id: str, *, repo_root: Path | None = None
) -> object | None:
    """Import a module's ``exec.py``, preferring its canonical dotted path.

    It used to always go through ``spec_from_file_location`` under a synthetic
    ``wos_module_exec_<id>`` name and never register the result in
    ``sys.modules``. Any exec that another module also imports normally —
    ``war_academy/exec.py`` imports ``research_center.exec`` — therefore existed
    as TWO module objects built from the same file, each with its own copy of
    the module-level tables and its own ``lru_cache``. Harmless while everything
    up there is immutable; a live bug the first time one of them warms a cache
    or mutates module state.

    The synthetic path stays as a fallback for a file that is not importable by
    name (outside the repo, or a directory component that is not an identifier).
    Registering the synthetic module in ``sys.modules`` would NOT have fixed
    this: the name still would not match the canonical one.
    """
    dotted = _dotted_name(exec_path, repo_root or default_repo_root())
    if dotted:
        try:
            mod = importlib.import_module(dotted)
        except Exception:
            logger.warning(
                "module exec: canonical import of %s failed — falling back to "
                "a file-path load, which yields a module object distinct from "
                "any normal import of the same file",
                dotted,
                exc_info=True,
            )
        else:
            if _exports_handlers(mod):
                return mod
            # Partially-initialised: this exec is ABOVE us on the import stack.
            # `arena/exec.py` imports `tasks.dsl_exec.context`, which pulls in
            # `tasks.dsl_exec.registry`, whose module level builds the registry
            # and lands right back here — with arena's own module object still
            # mid-execution, so the handler dict at the bottom of the file does
            # not exist yet. The old file-path load never hit this because it
            # ran a FRESH copy every time; the duplication we are removing was
            # accidentally load-bearing for exactly this cycle. Fall back to a
            # private copy for these, and only these.
            logger.debug(
                "module exec: %s is mid-import (circular) — using a private copy",
                dotted,
            )

    mod_name = f"wos_module_exec_{module_id}"
    spec = importlib.util.spec_from_file_location(mod_name, exec_path)
    if spec is None or spec.loader is None:
        logger.warning("module exec: failed to build spec for %s", exec_path)
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handlers_from_module(mod: object, module_id: str) -> dict[str, DslExecHandler]:
    raw = getattr(mod, "DSL_EXEC_HANDLERS", None)
    if raw is None:
        raw = getattr(mod, "EXEC_HANDLERS", None)
    if not isinstance(raw, dict):
        logger.warning(
            "module exec: %s has no DSL_EXEC_HANDLERS dict — skipping",
            module_id,
        )
        return {}
    out: dict[str, DslExecHandler] = {}
    for key, fn in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        if not callable(fn):
            logger.warning(
                "module exec: %s handler %r is not callable — skipping",
                module_id,
                name,
            )
            continue
        out[name] = fn  # type: ignore[assignment]
    return out


def load_module_exec_handlers(
    repo_root: Path | None = None,
    *,
    game: str | None = None,
) -> dict[str, DslExecHandler]:
    """Discover and import every module ``exec.py`` (or ``module.yaml`` ``exec:`` path)."""
    ensure_repo_on_sys_path()
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    merged: dict[str, DslExecHandler] = {}
    for module_dir in iter_module_dirs(root, game=game):
        meta = _load_module_yaml(module_dir)
        module_id = module_meta_id(module_dir)
        exec_decl = str(meta.get("exec") or "exec.py").strip() or "exec.py"
        exec_path = (module_dir / exec_decl).resolve()
        if not exec_path.is_file():
            continue
        mod = _import_exec_module(exec_path, module_id)
        if mod is None:
            continue
        handlers = _handlers_from_module(mod, module_id)
        for name, fn in handlers.items():
            if name in merged:
                logger.warning(
                    "module exec: duplicate handler %r (module %s overrides)",
                    name,
                    module_id,
                )
            merged[name] = fn
        if handlers:
            logger.debug(
                "module exec: loaded %d handler(s) from %s",
                len(handlers),
                module_id,
            )
    return merged
