"""Load DSL ``exec:`` handlers contributed by feature modules.

Each module may ship ``exec.py`` (or a path declared in ``module.yaml`` as
``exec: <relative-path>``) exporting::

    DSL_EXEC_HANDLERS: dict[str, DslExecHandler]

Handlers are merged into :data:`tasks.dsl_exec.DSL_EXEC_REGISTRY` after the
core registry. Duplicate names log a warning; the later module in sorted
``module_id`` order wins.
"""
from __future__ import annotations

import importlib.util
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import yaml

from config.module_discovery import iter_module_dirs, module_meta_id
from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

DslExecHandler = Callable[[Any], Awaitable[None]]



def _load_module_yaml(module_dir: Path) -> dict[str, object]:
    path = module_dir / "module.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _import_exec_module(exec_path: Path, module_id: str) -> object | None:
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


def load_module_exec_handlers(repo_root: Path | None = None) -> dict[str, DslExecHandler]:
    """Discover and import every module ``exec.py`` (or ``module.yaml`` ``exec:`` path)."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    merged: dict[str, DslExecHandler] = {}
    for module_dir in iter_module_dirs(root):
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
