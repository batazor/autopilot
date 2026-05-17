"""Module scope filtering (All / Core / feature module)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    get_wiki_module,
    module_scope_options,
    normalize_module_scope,
    path_matches_module_scope,
)
from scenarios.registry import scenario_roots

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_module_scope_defaults_to_all() -> None:
    assert normalize_module_scope(None) == ALL_MODULES_KEY
    assert normalize_module_scope("") == ALL_MODULES_KEY


def test_scenario_roots_filter_core_only(tmp_path: Path) -> None:
    core = tmp_path / "modules" / "core" / "bootstrap_probe"
    (core / "scenarios").mkdir(parents=True)
    (core / "module.yaml").write_text("id: bootstrap_probe\ntitle: Bootstrap\n", encoding="utf-8")
    (core / "scenarios" / "a.yaml").write_text("steps: []\n", encoding="utf-8")
    mod = tmp_path / "modules" / "mail"
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
    core = tmp_path / "modules" / "core" / "bootstrap_probe" / "scenarios" / "x.yaml"
    core.parent.mkdir(parents=True)
    (tmp_path / "modules" / "core" / "bootstrap_probe" / "module.yaml").write_text(
        "id: bootstrap_probe\n", encoding="utf-8"
    )
    core.write_text("", encoding="utf-8")
    nested = tmp_path / "modules" / "core" / "bootstrap_probe" / "scenarios" / "z.yaml"
    nested.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "modules" / "core" / "bootstrap_probe" / "module.yaml").write_text(
        "id: bootstrap_probe\n", encoding="utf-8"
    )
    nested.write_text("", encoding="utf-8")
    mod = tmp_path / "modules" / "vip" / "scenarios" / "y.yaml"
    mod.parent.mkdir(parents=True)
    (tmp_path / "modules" / "vip" / "module.yaml").write_text("id: vip\n", encoding="utf-8")
    mod.write_text("", encoding="utf-8")

    assert path_matches_module_scope(core, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(nested, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(mod, tmp_path, ALL_MODULES_KEY)
    assert path_matches_module_scope(core, tmp_path, CORE_MODULE_KEY)
    assert path_matches_module_scope(nested, tmp_path, CORE_MODULE_KEY)
    assert not path_matches_module_scope(mod, tmp_path, CORE_MODULE_KEY)
    assert path_matches_module_scope(mod, tmp_path, "vip")
    assert not path_matches_module_scope(core, tmp_path, "vip")


def test_iter_module_dirs_discovers_nested_module_yaml(tmp_path: Path) -> None:
    from config.module_discovery import iter_module_dirs, module_storage_key

    event_pkg = tmp_path / "modules" / "core" / "event"
    seven = event_pkg / "events" / "7-day"
    trials = tmp_path / "modules" / "events" / "trials"
    draft = tmp_path / "modules" / "draft" / "scratch"
    core_draft = tmp_path / "modules" / "core" / "draft" / "scratch"
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
    assert module_storage_key(seven, tmp_path) == "core/event/events/7-day"
    assert module_storage_key(trials, tmp_path) == "events/trials"


def test_path_matches_module_scope_event_child(tmp_path: Path) -> None:
    event_pkg = tmp_path / "modules" / "core" / "event"
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


def test_module_scope_options_includes_all_and_core(tmp_path: Path) -> None:
    (tmp_path / "area.json").write_text("{}", encoding="utf-8")
    keys = [k for k, _ in module_scope_options(tmp_path)]
    assert keys[0] == ALL_MODULES_KEY
    assert keys[1] == CORE_MODULE_KEY
