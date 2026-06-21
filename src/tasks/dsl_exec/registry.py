"""Exec-handler registry: core handlers + per-module ``exec.py`` contributions."""
from __future__ import annotations

from typing import TYPE_CHECKING

from tasks.dsl_exec.dismiss_popup import _exec_dismiss_popup
from tasks.dsl_exec.fetch_player import _exec_fetch_player
from tasks.dsl_exec.red_dots import (
    _exec_click_next_red_dot_tab,
    _exec_put_all_red_dots,
)
from tasks.dsl_exec.sync_state import (
    _exec_sync_building_name,
    _exec_sync_furnace_level,
)
from tasks.dsl_exec.use_all import _exec_drain_use_all

if TYPE_CHECKING:
    from pathlib import Path

    from tasks.dsl_exec.context import DslExecHandler

_CORE_DSL_EXEC_REGISTRY: dict[str, DslExecHandler] = {
    "fetch_player": _exec_fetch_player,
    "sync_building_name": _exec_sync_building_name,
    "sync_furnace_level": _exec_sync_furnace_level,
    # sync_hero_unit + scan_heroes_grid are contributed by the heroes module's
    # exec.py (games/wos/heroes/heroes/exec.py), auto-merged below.
    "click_next_red_dot_tab": _exec_click_next_red_dot_tab,
    "advance_tab_strip": _exec_click_next_red_dot_tab,
    "put_all_red_dots": _exec_put_all_red_dots,
    "dismiss_popup": _exec_dismiss_popup,
    "drain_use_all": _exec_drain_use_all,
}


def build_dsl_exec_registry(repo_root: Path | None = None) -> dict[str, DslExecHandler]:
    """Core handlers plus optional ``modules/<id>/exec.py`` contributions."""
    from century.gift_codes.exec import DSL_EXEC_HANDLERS as GIFT_CODES_HANDLERS
    from config.module_exec_registry import load_module_exec_handlers

    return {
        **_CORE_DSL_EXEC_REGISTRY,
        **GIFT_CODES_HANDLERS,
        **load_module_exec_handlers(repo_root),
    }


DSL_EXEC_REGISTRY: dict[str, DslExecHandler] = build_dsl_exec_registry()
