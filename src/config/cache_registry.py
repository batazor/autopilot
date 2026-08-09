"""One place that knows how to drop every in-process CONFIG cache.

`reload_config` promised to "drop all in-process config caches" and called
three helpers out of the seventeen that exist. The gap was not theoretical:
`api.services.modules_api.create_module` writes a new `module.yaml`, clears the
discovery caches, and the new module's scenarios stay invisible to that same
process until it restarts — the helper that would fix it
(`dsl.registry._clear_scenario_root_caches`) had no caller anywhere.

Entries are ``"module:function"`` strings resolved lazily at clear time rather
than callables registered at import. Two reasons:

* no import cycles, and no dependence on import order — a module that was never
  imported has no warm cache to drop, so failing to resolve it is correct;
* the list reads as an inventory. A reviewer can see what is covered without
  chasing decorators through eight files.

Scope is caches over **files on disk** — module manifests, area docs, scenario
trees, screen-verify configs, static game data. Redis-state clears
(`clear_focus`, `clear_pending`, dismissed-attention keys) are not caches and
are not here; neither are device/frame caches, which belong to a device's
lifetime rather than to config.
"""

from __future__ import annotations

import logging
from contextlib import suppress

logger = logging.getLogger(__name__)

# module path : zero-arg callable that drops one cache family.
CONFIG_CACHE_CLEARERS: tuple[str, ...] = (
    # module discovery + manifests
    "config.module_discovery:_clear_module_discovery_caches",
    "config.capture_rate:clear_capture_rate_cache",
    "config.test_module:clear_test_module_cache",
    # area docs and the indexes derived from them
    "layout.area_manifest:clear_area_doc_cache",
    "layout.area_lookup:clear_region_lookup_cache",
    "layout.tabs_strip_identifier:clear_tab_template_cache",
    # scenario tree
    "dsl.registry:_clear_scenario_root_caches",
    "dsl.registry:_clear_scenario_allowlist_cache",
    "dsl.template_resolver:_clear_template_resolver_caches",
    # overlay rules
    "analysis.overlay_manifest:clear_merged_analyze_yaml_cache",
    # navigation graph
    "navigation.screen_graph:invalidate_edge_taps_cache",
    "navigation.screen_graph:invalidate_screen_verify_config",
    # static game data registries
    "config.research:invalidate_research_registry",
    "config.research:invalidate_alliance_tech_registry",
    "config.heroes:invalidate_hero_registry",
    "config.items:invalidate_item_registry",
    "config.buildings:invalidate_building_registry",
    "config.devices:invalidate_device_registry",
    "optimizer.context:invalidate_balance_context",
)


def clear_all() -> None:
    """Drop every registered config cache. Never raises.

    A clearer that cannot be imported is skipped: its module was never loaded,
    so by definition it holds nothing.
    """
    import importlib

    for entry in CONFIG_CACHE_CLEARERS:
        module_name, _, attr = entry.partition(":")
        with suppress(Exception):
            fn = getattr(importlib.import_module(module_name), attr, None)
            if callable(fn):
                fn()
                continue
            logger.debug("cache_registry: %s is not callable — skipping", entry)
