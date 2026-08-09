"""`reload_config` must actually drop every config cache.

It promised "all in-process config caches" and called three helpers out of the
seventeen that existed. That gap was reachable: `create_module` wrote a new
`module.yaml`, cleared discovery, and the module's scenarios stayed invisible to
that same process until restart — the helper that would have fixed it had no
caller anywhere in the tree.

The inventory is only trustworthy if something checks it, hence the sweep below.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from config.cache_registry import CONFIG_CACHE_CLEARERS, clear_all

if TYPE_CHECKING:
    import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"

# Clear helpers that are NOT config caches. Redis-state resets, per-device
# runtime state and test-only fixtures do not belong to `reload_config`; listing
# them here is a deliberate exclusion, not an oversight.
_NOT_CONFIG_CACHES = {
    "agentctl.core:clear_focus",
    "config.devices:clear_last_active_player",
    "config.devices_db:clear_last_active_player",
    "dashboard.farm_handoff:clear_pending",
    "dashboard.redis_client:clear_queue_tasks",
    "api.routers.farm:clear_registration_log",
    "api.services.dashboard_rev:invalidate_revision",
    "api.services.dashboard_rev:invalidate_revision_for_topic",
    "api.services.click_approval_store:clear_pending",
    "api.services.click_approval_store:clear_queue_all",
    "api.services.click_approval_store:clear_stale_pending",
    "worker.focus_mode:clear_focus",
    "modules.radar.events:clear_stop",
    "modules.radar.events:clear_active",
    "modules.broadcast.db:clear_all_for_tests",
}


def _module_name(path: Path) -> str:
    return path.relative_to(_SRC).with_suffix("").as_posix().replace("/", ".")


def _public_clear_helpers() -> set[str]:
    """``module:function`` for every top-level clear/invalidate helper in src."""
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        rel = path.as_posix()
        # The registry itself is the fan-out, not a cache; `reload.py` is its
        # one public caller.
        if "/tests/" in rel or path.name in ("cache_registry.py", "reload.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in tree.body:  # top level only — nested helpers are private
            if not isinstance(node, ast.FunctionDef):
                continue
            name = node.name
            if not (
                name.startswith(("clear_", "_clear_", "invalidate_"))
                or name.endswith("_cache_clear")
            ):
                continue
            # A clear helper takes no required arguments; anything that needs a
            # client or an id is operating on state, not on a cache.
            required = len(node.args.args) - len(node.args.defaults)
            if required > 0 or node.args.kwonlyargs:
                continue
            found.add(f"{_module_name(path)}:{name}")
    return found


def test_every_registered_entry_resolves_to_a_callable() -> None:
    unresolvable = []
    for entry in CONFIG_CACHE_CLEARERS:
        module_name, _, attr = entry.partition(":")
        try:
            fn = getattr(importlib.import_module(module_name), attr, None)
        except Exception as exc:
            unresolvable.append(f"{entry} ({exc})")
            continue
        if not callable(fn):
            unresolvable.append(entry)

    assert not unresolvable, f"registered but not callable: {unresolvable}"


def test_no_config_cache_clearer_is_left_out() -> None:
    """A new `@lru_cache` over config files must join the registry.

    If this fails, either add the helper to `CONFIG_CACHE_CLEARERS` or, when it
    is not a config cache, to `_NOT_CONFIG_CACHES` with the reason.
    """
    registered = set(CONFIG_CACHE_CLEARERS)
    orphans = sorted(_public_clear_helpers() - registered - _NOT_CONFIG_CACHES)

    assert not orphans, (
        "clear helpers known to neither list:\n  " + "\n  ".join(orphans)
    )


def test_entries_are_unique() -> None:
    assert len(CONFIG_CACHE_CLEARERS) == len(set(CONFIG_CACHE_CLEARERS))


def test_clear_all_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker must not die because a cache reset failed."""
    import config.cache_registry as reg

    monkeypatch.setattr(
        reg, "CONFIG_CACHE_CLEARERS", ("no.such.module:nope", "config.reload:missing")
    )

    clear_all()


def test_reload_config_uses_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """`reload_config` must stay a thin fan-out — hand-listing helpers there is
    exactly how it drifted to 3-of-17."""
    import config.cache_registry as reg
    from config.reload import reload_config

    called: list[bool] = []
    monkeypatch.setattr(reg, "clear_all", lambda: called.append(True))

    reload_config()

    assert called == [True]


def test_scenario_tree_caches_are_covered() -> None:
    """The specific gap that made `create_module` broken."""
    registered = set(CONFIG_CACHE_CLEARERS)

    assert "dsl.registry:_clear_scenario_root_caches" in registered
    assert "dsl.template_resolver:_clear_template_resolver_caches" in registered


def test_clear_all_actually_empties_a_warm_cache() -> None:
    """End-to-end: warm a real cache, clear, confirm the entry count dropped."""
    from config.capture_rate import _module_capture_ms_map
    from config.paths import repo_root

    _module_capture_ms_map(str(repo_root().resolve()), "wos")
    assert _module_capture_ms_map.cache_info().currsize > 0

    clear_all()

    assert _module_capture_ms_map.cache_info().currsize == 0


def test_a_scenario_added_at_runtime_becomes_visible_after_reload(tmp_path) -> None:
    """The regression `create_module` hit, reproduced against the real tree.

    Warm the scenario caches, drop a new module on disk, and confirm the
    scenario is invisible until `reload_config` — and visible after. Clearing
    only the discovery caches (what `create_module` used to do) leaves this
    count unchanged, because the scenario tree has caches of its own.
    """
    from config.paths import repo_root
    from config.reload import reload_config
    from dsl.registry import iter_scenario_yaml_files

    root = repo_root()
    probe = root / "games" / "wos" / "core" / "_cache_registry_probe"
    try:
        warm = len(iter_scenario_yaml_files(root))

        (probe / "scenarios").mkdir(parents=True)
        (probe / "module.yaml").write_text("id: _cache_registry_probe\n", encoding="utf-8")
        (probe / "scenarios" / "_probe.yaml").write_text(
            "enabled: true\nname: probe\nsteps: [{wait: 1ms}]\n", encoding="utf-8"
        )

        assert len(iter_scenario_yaml_files(root)) == warm, "cache was not warm"

        reload_config()

        assert len(iter_scenario_yaml_files(root)) == warm + 1
    finally:
        import shutil

        shutil.rmtree(probe, ignore_errors=True)
        reload_config()
