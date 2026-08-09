"""Single entrypoint to invalidate every in-process config cache.

Config files (``area.json``, ``modules/**/area.yaml``, ``modules/**/screen_verify.yaml``,
``module.yaml``) are loaded once and cached for the process lifetime — the
labeling editor / dashboard reload button calls :func:`reload_config` after a
mutation so the next overlay tick sees fresh state.
"""
from __future__ import annotations


def reload_config() -> None:
    """Drop all in-process config caches.

    The inventory lives in :mod:`config.cache_registry`; this function is the
    only fan-out. It used to hand-call three helpers while seventeen existed,
    which made the docstring above a lie — a module created at runtime stayed
    invisible to its own process because the scenario-tree caches were never in
    the list.

    Safe to call from any thread/coroutine, and never raises. After the call,
    the next access re-reads from disk. Each worker process keeps its own
    caches — cross-process reload should be coordinated via Redis (out of scope
    here).
    """
    from config.cache_registry import clear_all

    clear_all()
