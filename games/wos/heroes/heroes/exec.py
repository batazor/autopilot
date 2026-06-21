"""DSL ``exec:`` handlers contributed by the heroes module.

Co-locates the hero-specific handlers with the module they serve. They are
auto-discovered and merged into ``tasks.dsl_exec.DSL_EXEC_REGISTRY`` by
``config.module_exec_registry`` at registry build time.

- ``scan_heroes_grid`` — snapshot every visible hero on the grid into state.
- ``sync_hero_unit``   — snapshot the currently-open hero card into state.

The implementations live in sibling modules so the (large, well-tested)
grid scanner keeps its own module + test file.
"""
from __future__ import annotations

from games.wos.heroes.heroes.scan_heroes_grid import _exec_scan_heroes_grid
from games.wos.heroes.heroes.sync_hero_unit import _exec_sync_hero_unit

DSL_EXEC_HANDLERS = {
    "scan_heroes_grid": _exec_scan_heroes_grid,
    "sync_hero_unit": _exec_sync_hero_unit,
}
