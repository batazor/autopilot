"""Single entrypoint to invalidate every in-process config cache.

Config files (``area.json``, ``modules/**/area.yaml``, ``modules/**/screen_verify.yaml``,
``module.yaml``) are loaded once and cached for the process lifetime — the
labeling editor / dashboard reload button calls :func:`reload_config` after a
mutation so the next overlay tick sees fresh state.
"""
from __future__ import annotations


def reload_config() -> None:
    """Drop all in-process config caches.

    Safe to call from any thread/coroutine. After the call, the next access
    re-reads YAML/JSON from disk. Each worker process keeps its own caches —
    cross-process reload should be coordinated via Redis (out of scope here).
    """
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache
    from navigation.screen_graph import invalidate_screen_verify_config

    _clear_module_discovery_caches()
    invalidate_screen_verify_config()
    clear_area_doc_cache()
