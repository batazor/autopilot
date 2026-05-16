from __future__ import annotations

from pathlib import Path
from typing import Any

import navigation.screen_graph as screen_graph


def test_screen_verify_config_loads_rules_from_yaml(monkeypatch: Any, tmp_path: Path) -> None:
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
    monkeypatch.setattr(screen_graph, "_screen_verify_yaml_path", lambda: cfg)
    screen_graph.load_screen_verify_config.cache_clear()

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
        screen_graph.load_screen_verify_config.cache_clear()


def test_screen_verify_config_merges_module_yaml(monkeypatch: Any, tmp_path: Path) -> None:
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
    module_cfg = tmp_path / "modules" / "core" / "chief_profile" / "screen_verify.yaml"
    module_cfg.parent.mkdir(parents=True)
    module_cfg.write_text(
        """
screens:
  chief_profile:
    landmarks:
      - match: chief_profile_title
        threshold: 0.9
    rules:
      - match: chief_profile_title
        threshold: 0.9
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(screen_graph, "_screen_verify_yaml_paths", lambda: [root_cfg, module_cfg])
    screen_graph.load_screen_verify_config.cache_clear()

    try:
        assert screen_graph.screen_verify_rules("main_city") == [{"match": "icon.world"}]
        assert screen_graph.screen_verify_rules("chief_profile") == [
            {"match": "chief_profile_title", "threshold": 0.9}
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


def test_production_screen_verify_yaml_contains_chief_profile_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        landmarks = screen_graph.screen_landmark_rules("chief_profile")
        rules = screen_graph.screen_verify_rules("chief_profile")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    expected = [{"match": "chief_profile_title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_main_city_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        landmarks = screen_graph.screen_landmark_rules("main_city")
        rules = screen_graph.screen_verify_rules("main_city")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    expected = [{"match": "icon.world", "threshold": 0.9}]
    assert {"match": "icon.world"} in landmarks
    assert rules == expected


def test_production_screen_verify_yaml_active_rules_are_template_matches() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        screens = screen_graph.load_screen_verify_config().get("screens")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert isinstance(screens, dict)
    for entry in screens.values():
        assert isinstance(entry, dict)
        for rule in [*(entry.get("landmarks") or []), *(entry.get("rules") or [])]:
            assert "match" in rule
            assert "ocr" not in rule


def test_production_screen_verify_yaml_contains_welcome_back_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        landmarks = screen_graph.screen_landmark_rules("welcome_back")
        rules = screen_graph.screen_verify_rules("welcome_back")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    expected = [
        {"match": "text.welcome_back", "threshold": 0.9}
    ]
    assert landmarks == expected
    assert rules == expected
