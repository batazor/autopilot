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

text_switch:
  - ocr: page_title
    threshold: 0.8
    cases:
      arena: [arena]

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
        assert screen_graph.screen_text_switch_rules() == [
            {"ocr": "page_title", "cases": {"arena": ["arena"]}, "threshold": 0.8}
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


def test_production_screen_verify_yaml_contains_chief_profile_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        rules = screen_graph.screen_verify_rules("chief_profile")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert rules == [
        {
            "ocr": "page_title",
            "contains": ["chief profile", "chief", "profile"],
            "threshold": 0.8,
        }
    ]


def test_production_screen_verify_yaml_contains_mail_title_switch() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        rules = screen_graph.screen_text_switch_rules()
        mail_rules = screen_graph.screen_verify_rules("mail")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()

    assert any("mail" in rule.get("cases", {}) for rule in rules)
    assert mail_rules == [
        {"ocr": "page_title", "contains": ["mail"], "threshold": 0.8}
    ]
