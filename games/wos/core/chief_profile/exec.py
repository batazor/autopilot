"""DSL ``exec:`` handlers contributed by the chief_profile module.

Auto-discovered and merged into ``tasks.dsl_exec.DSL_EXEC_REGISTRY`` by
``config.module_exec_registry`` at registry build time.

- ``sync_troop_pool`` — read live troop counts off the Troops Preview screen
  (chief_profile → Troops) into ``troops.<type>.available``.
"""
from __future__ import annotations

from games.wos.core.chief_profile.sync_troop_pool import _exec_sync_troop_pool

DSL_EXEC_HANDLERS = {
    "sync_troop_pool": _exec_sync_troop_pool,
}
