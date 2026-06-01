"""Module scope filtering (All / Core / feature module)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    get_wiki_module,
    module_scope_options,
    normalize_module_scope,
    path_matches_module_scope,
)
from dsl.registry import scenario_roots

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_module_scope_defaults_to_all() -> None:
    assert normalize_module_scope(None) == ALL_MODULES_KEY
    assert normalize_module_scope("") == ALL_MODULES_KEY


def test_scenario_roots_filter_core_only(tmp_path: Path) -> None:
    core = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "bootstrap_probe"
    (core / "scenarios").mkdir(parents=True)
    (core / "module.yaml").write_text("id: bootstrap_probe\ntitle: Bootstrap\n", encoding="utf-8")
    (core / "scenarios" / "a.yaml").write_text("steps: []\n", encoding="utf-8")
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "mail"
    (mod / "scenarios").mkdir(parents=True)
    (mod / "module.yaml").write_text("id: mail\ntitle: Mail\n", encoding="utf-8")
    (mod / "scenarios" / "read.yaml").write_text("steps: []\n", encoding="utf-8")

    all_roots = scenario_roots(tmp_path, ALL_MODULES_KEY)
    assert len(all_roots) == 2
    core_only = scenario_roots(tmp_path, CORE_MODULE_KEY)
    assert len(core_only) == 1
    assert core_only[0].module_id == "bootstrap_probe"
    mail_only = scenario_roots(tmp_path, "mail")
    assert len(mail_only) == 1
    assert mail_only[0].module_id == "mail"


def test_path_matches_module_scope(tmp_path: Path) -> None:
    core = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "bootstrap_probe" / "scenarios" / "x.yaml"
    core.parent.mkdir(parents=True)
    (_modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "bootstrap_probe" / "module.yaml").write_text(
        "id: bootstrap_probe\n", encoding="utf-8"
    )
    core.write_text("", encoding="utf-8")
    nested = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "bootstrap_probe" / "scenarios" / "z.yaml"
    nested.parent.mkdir(parents=True, exist_ok=True)
    (_modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "bootstrap_probe" / "module.yaml").write_text(
        "id: bootstrap_probe\n", encoding="utf-8"
    )
    nested.write_text("", encoding="utf-8")
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "vip" / "scenarios" / "y.yaml"
    mod.parent.mkdir(parents=True)
    (_modules_root_for(_default_game(), repo_root=tmp_path) / "vip" / "module.yaml").write_text("id: vip\n", encoding="utf-8")
    mod.write_text("", encoding="utf-8")

    assert path_matches_module_scope(core, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(nested, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(mod, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(core, tmp_path, CORE_MODULE_KEY)
    assert path_matches_module_scope(nested, tmp_path, CORE_MODULE_KEY)
    assert not path_matches_module_scope(mod, tmp_path, CORE_MODULE_KEY)
    assert path_matches_module_scope(mod, tmp_path, "vip")
    assert not path_matches_module_scope(core, tmp_path, "vip")


def test_path_matches_all_scope_is_game_scoped(tmp_path: Path) -> None:
    wos = _modules_root_for("wos", repo_root=tmp_path) / "core" / "main_city" / "scenarios" / "wos.yaml"
    kingshot = _modules_root_for("kingshot", repo_root=tmp_path) / "core" / "main_city" / "scenarios" / "ks.yaml"
    for path in (wos, kingshot):
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")

    assert path_matches_module_scope(kingshot, tmp_path, ALL_MODULES_KEY, game="kingshot")
    assert not path_matches_module_scope(wos, tmp_path, ALL_MODULES_KEY, game="kingshot")
    assert path_matches_module_scope(wos, tmp_path, ALL_MODULES_KEY, game="wos")
    assert not path_matches_module_scope(kingshot, tmp_path, ALL_MODULES_KEY, game="wos")


def test_iter_module_dirs_discovers_nested_module_yaml(tmp_path: Path) -> None:
    from config.module_discovery import iter_module_dirs, module_storage_key

    event_pkg = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "event"
    seven = event_pkg / "events" / "7-day"
    trials = _modules_root_for(_default_game(), repo_root=tmp_path) / "events" / "trials"
    draft = _modules_root_for(_default_game(), repo_root=tmp_path) / "draft" / "scratch"
    core_draft = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "draft" / "scratch"
    (seven / "scenarios").mkdir(parents=True)
    (trials / "scenarios").mkdir(parents=True)
    (draft / "scenarios").mkdir(parents=True)
    (core_draft / "scenarios").mkdir(parents=True)
    (event_pkg / "module.yaml").write_text("id: event\nwiki: false\n", encoding="utf-8")
    (seven / "module.yaml").write_text("id: 7-day\nwiki: false\n", encoding="utf-8")
    (trials / "module.yaml").write_text("id: trials\nwiki: false\n", encoding="utf-8")
    (draft / "module.yaml").write_text("id: draft_scratch\nwiki: false\n", encoding="utf-8")
    (core_draft / "module.yaml").write_text(
        "id: core_draft_scratch\nwiki: false\n", encoding="utf-8"
    )
    (seven / "analyze").mkdir()
    (seven / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")

    dirs = list(iter_module_dirs(tmp_path))
    assert event_pkg in dirs
    assert seven in dirs
    assert trials in dirs
    assert draft not in dirs
    assert core_draft not in dirs
    assert module_storage_key(seven, tmp_path) == "wos:core/event/events/7-day"
    assert module_storage_key(trials, tmp_path) == "wos:events/trials"


def test_path_matches_module_scope_event_child(tmp_path: Path) -> None:
    event_pkg = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "event"
    seven = event_pkg / "events" / "7-day"
    nested = seven / "scenarios" / "x.yaml"
    nested.parent.mkdir(parents=True)
    (event_pkg / "module.yaml").write_text("id: event\n", encoding="utf-8")
    (seven / "module.yaml").write_text("id: 7-day\n", encoding="utf-8")
    nested.write_text("", encoding="utf-8")

    assert path_matches_module_scope(nested, tmp_path, "7-day")
    assert path_matches_module_scope(nested, tmp_path, CORE_MODULE_KEY)
    assert path_matches_module_scope(nested, tmp_path, "event")


def test_get_wiki_module_all_context(tmp_path: Path) -> None:
    (tmp_path / "area.json").write_text('{"screens": []}', encoding="utf-8")
    ctx = get_wiki_module(tmp_path, ALL_MODULES_KEY)
    assert ctx.is_all is True
    assert ctx.storage_key == ALL_MODULES_KEY


def test_module_scope_options_starts_with_all(tmp_path: Path) -> None:
    keys = [k for k, _ in module_scope_options(tmp_path)]
    assert keys[0] == ALL_MODULES_KEY
    assert CORE_MODULE_KEY not in keys  # Core scope no longer enumerated
