from __future__ import annotations

from typing import TYPE_CHECKING

import config.module_discovery as module_discovery
import config.paths as paths
import layout.area_manifest as area_manifest
import navigation.screen_graph as screen_graph
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def _isolate_from_real_repo(mocker, tmp_path: Path) -> None:
    """Point repo_root at ``tmp_path`` so production area.yaml files don't leak in."""
    mocker.patch.object(paths, "repo_root", new=lambda: tmp_path)
    module_discovery._clear_module_discovery_caches()
    area_manifest.clear_area_doc_cache()


def test_screen_verify_config_loads_rules_from_yaml(mocker, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
retry:
  attempts: 9
  interval_seconds: 1.25

screens:
  chief_profile:
    landmarks:
      - ocr: page_title
        contains: [chief]
    retry:
      attempts: 12
      interval_seconds: 2.5
    rules:
      - ocr: page_title
        contains: [chief, profile]
        threshold: 0.8
  arena:
    - ocr: page_title
      contains: arena
""",
        encoding="utf-8",
    )
    _isolate_from_real_repo(mocker, tmp_path)
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.screen_verify_retry() == (9, 1.25)
        assert screen_graph.screen_verify_retry("arena") == (9, 1.25)
        assert screen_graph.screen_verify_retry("chief_profile") == (12, 2.5)
        assert screen_graph.screen_verify_rules("chief_profile") == [
            {"ocr": "page_title", "contains": ["chief", "profile"], "threshold": 0.8},
        ]
        assert screen_graph.screen_verify_rules("arena") == [
            {"ocr": "page_title", "contains": "arena"}
        ]
        assert screen_graph.screen_landmark_rules("chief_profile") == [
            {"ocr": "page_title", "contains": ["chief"]}
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_screen_verify_rules_only_mirrors_landmarks(mocker, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text(
        """
screens:
  loading:
    rules:
      - match: text.survival
        threshold: 0.9
""",
        encoding="utf-8",
    )
    _isolate_from_real_repo(mocker, tmp_path)
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        expected = [{"match": "text.survival", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("loading") == expected
        assert screen_graph.screen_landmark_rules("loading") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_screen_verify_config_merges_module_yaml(mocker, tmp_path: Path) -> None:
    root_cfg = tmp_path / "screen_verify.yaml"
    root_cfg.write_text(
        """
retry:
  attempts: 6
  interval_seconds: 0.8
screens:
  main_city:
    rules:
      - match: icon.world
""",
        encoding="utf-8",
    )
    module_cfg = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "chief_profile" / "screen_verify.yaml"
    module_cfg.parent.mkdir(parents=True)
    module_cfg.write_text(
        """
screens:
  chief_profile:
    rules:
      - match: chief_profile_title
        threshold: 0.9
""",
        encoding="utf-8",
    )
    _isolate_from_real_repo(mocker, tmp_path)
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [root_cfg, module_cfg])
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.screen_verify_rules("main_city") == [{"match": "icon.world"}]
        chief_expected = [{"match": "chief_profile_title", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("chief_profile") == chief_expected
        assert screen_graph.screen_landmark_rules("chief_profile") == chief_expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_screen_verify_config_handles_no_enabled_modules(mocker, tmp_path: Path) -> None:
    _isolate_from_real_repo(mocker, tmp_path)
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=list)
    mocker.patch.object(screen_graph, "_area_yaml_paths", new=list)
    mocker.patch.object(screen_graph, "_hero_ids", new=list)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.load_screen_verify_config() == {"retry": {}, "screens": {}}
        assert screen_graph.screen_verify_screen_names() == []
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_production_screen_verify_yaml_contains_hero_recruitment_route_nodes() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        names = set(screen_graph.screen_verify_screen_names())
        hero_rules = screen_graph.screen_verify_rules("heroes")
        recruit_rules = screen_graph.screen_verify_rules("hero.recruitment")
        route = screen_graph.bfs_route("shop.dawn_market", "hero.recruitment")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert "heroes" in names
    assert "hero.recruitment" in names
    # Both nodes must be *detectable* (non-empty rules) for the route to be
    # walkable — the rule CONTENT is production YAML and is not restated here.
    assert hero_rules
    assert recruit_rules
    assert route == ["shop.dawn_market", "main_city", "heroes", "hero.recruitment"]


def test_production_screen_verify_yaml_active_rules_are_recognised_forms() -> None:
    """Every active verification rule must be a recognised, deterministic form:

    * ``match`` — template / icon match (the default, fast path);
    * ``from_screen`` — navigation-history check (synthesised for per-hero wiki
      nodes), no pixels;
    * ``tab_active`` — tab-capsule active-state check;
    * ``ocr`` **paired with** ``contains`` — a specific text check, the only
      way to tell apart screens that share one region and differ solely by
      title text (e.g. the Labyrinth caves all reuse ``labyrinth.cave.title``
      and are distinguished by the cave name).

    A bare ``ocr`` without ``contains`` is rejected — it would assert "some text
    is here" without pinning which screen.
    """
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        screens = screen_graph.load_screen_verify_config().get("screens")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert isinstance(screens, dict)
    for entry in screens.values():
        assert isinstance(entry, dict)
        for rule in [*(entry.get("landmarks") or []), *(entry.get("rules") or [])]:
            is_template = (
                "match" in rule or "from_screen" in rule or "tab_active" in rule
            )
            is_text_content = "ocr" in rule and "contains" in rule
            assert is_template or is_text_content, rule
            if "ocr" in rule:
                assert "contains" in rule, rule


def test_synthesised_furnace_verify_rule_uses_furnace_name(mocker, tmp_path: Path) -> None:
    _isolate_from_real_repo(mocker, tmp_path)
    buildings_dir = tmp_path / "games" / "wos" / "db" / "buildings"
    buildings_dir.mkdir(parents=True)
    (buildings_dir / "index.yaml").write_text(
        """
buildings:
  - id: furnace
    name: Furnace
  - id: cookhouse
    name: Cookhouse
  - id: shelter
    name: Shelter
  - id: sawmill
    name: Sawmill
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=list)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        screens = screen_graph.load_screen_verify_config().get("screens")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    # The generator keeps the per-building ocr region (furnace.name / building.name
    # / shelter.title / building.title) and appends RU «Белая мгла» localisations
    # to contains from the building-name dictionary (case-insensitive OR).
    assert screens["furnace"]["rules"] == [
        {"ocr": "furnace.name", "contains": ["Furnace", "Печь", "Топка"], "threshold": 0.8}
    ]
    assert screens["cookhouse"]["rules"] == [
        {"ocr": "building.name", "contains": ["Cookhouse", "Кухня", "Столовая"], "threshold": 0.8}
    ]
    assert screens["shelter"]["rules"] == [
        {"ocr": "shelter.title", "contains": ["Shelter", "Барак"], "threshold": 0.8}
    ]
    assert screens["sawmill"]["rules"] == [
        {"ocr": "building.title", "contains": ["Sawmill", "Лесопилка"], "threshold": 0.8}
    ]


def test_production_screen_verify_yaml_does_not_split_claimed_by_ocr() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("claimed")
        rules = screen_graph.screen_verify_rules("claimed")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert landmarks == []
    assert rules == []


