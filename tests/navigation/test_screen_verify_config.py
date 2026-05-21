from __future__ import annotations

from typing import TYPE_CHECKING

import navigation.screen_graph as screen_graph

if TYPE_CHECKING:
    from pathlib import Path


def test_screen_verify_config_loads_rules_from_yaml(mocker, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    area = tmp_path / "area.json"
    area.write_text('{"screens":[]}', encoding="utf-8")
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    mocker.patch.object(screen_graph, "_area_json_path", new=lambda: area)
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
    area = tmp_path / "area.json"
    area.write_text('{"screens":[]}', encoding="utf-8")
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
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    mocker.patch.object(screen_graph, "_area_json_path", new=lambda: area)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        expected = [{"match": "text.survival", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("loading") == expected
        assert screen_graph.screen_landmark_rules("loading") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_screen_verify_config_merges_module_yaml(mocker, tmp_path: Path) -> None:
    root_cfg = tmp_path / "screen_verify.yaml"
    area = tmp_path / "area.json"
    area.write_text('{"screens":[]}', encoding="utf-8")
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
    rules:
      - match: chief_profile_title
        threshold: 0.9
""",
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [root_cfg, module_cfg])
    mocker.patch.object(screen_graph, "_area_json_path", new=lambda: area)
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.screen_verify_rules("main_city") == [{"match": "icon.world"}]
        chief_expected = [{"match": "chief_profile_title", "threshold": 0.9}]
        assert screen_graph.screen_verify_rules("chief_profile") == chief_expected
        assert screen_graph.screen_landmark_rules("chief_profile") == chief_expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_production_screen_verify_yaml_contains_chief_profile_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("chief_profile")
        rules = screen_graph.screen_verify_rules("chief_profile")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "chief_profile.title", "threshold": 0.9}]
    assert expected[0] in landmarks
    assert rules == expected


def test_production_screen_verify_yaml_contains_main_city_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("main_city")
        rules = screen_graph.screen_verify_rules("main_city")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "icon.world", "threshold": 0.9}]
    assert rules == expected
    # ``area.json`` may inject extra detection-only landmarks (e.g. main_city.title).
    assert expected[0] in landmarks


def test_production_screen_verify_yaml_active_rules_are_template_matches() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        screens = screen_graph.load_screen_verify_config().get("screens")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    assert isinstance(screens, dict)
    for entry in screens.values():
        assert isinstance(entry, dict)
        for rule in [*(entry.get("landmarks") or []), *(entry.get("rules") or [])]:
            # ``from_screen`` rules (synthesized for per-hero wiki nodes) check
            # navigation history instead of pixels and are exempt from the
            # "template match only" policy this test enforces.
            assert "match" in rule or "from_screen" in rule or "tab_active" in rule
            assert "ocr" not in rule


def test_production_screen_verify_yaml_contains_welcome_back_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("welcome_back")
        rules = screen_graph.screen_verify_rules("welcome_back")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [
        {"match": "text.welcome_back", "threshold": 0.9}
    ]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_loading_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("loading")
        rules = screen_graph.screen_verify_rules("loading")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "text.survival", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_mail_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("mail")
        rules = screen_graph.screen_verify_rules("mail")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "mail.title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_exploration_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("exploration")
        rules = screen_graph.screen_verify_rules("exploration")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "exploration.to.squad_settings", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_squad_settings_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("squad_settings")
        rules = screen_graph.screen_verify_rules("squad_settings")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [
        {"match": "squad_settings.quick_deploy", "threshold": 0.9},
        {"match": "squad_settings.fight", "threshold": 0.9},
    ]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_exploration_defeat_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("exploration.defeat")
        rules = screen_graph.screen_verify_rules("exploration.defeat")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "exploration.defeat.title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_mail_tab_rules() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        checks = {
            "mail.wars": "mail.tab.wars",
            "mail.alliance": "mail.tab.alliance",
            "mail.system": "mail.tab.system",
            "mail.reports": "mail.tab.reports",
            "mail.starred": "mail.tab.starred",
        }
        for screen, tab_region in checks.items():
            expected = [{"match": "mail.title", "threshold": 0.9, "tab_active": tab_region}]
            assert screen_graph.screen_landmark_rules(screen) == expected
            assert screen_graph.screen_verify_rules(screen) == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_production_screen_verify_yaml_contains_trials_day_rules() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        for day in range(1, 6):
            screen = f"event.trials.day.{day}"
            expected = [
                {
                    "match": "trials.title",
                    "threshold": 0.9,
                    "tab_active": f"trial.day.{day}",
                }
            ]
            assert screen_graph.screen_landmark_rules(screen) == expected
            assert screen_graph.screen_verify_rules(screen) == expected
        expected_base = [{"match": "trials.title", "threshold": 0.9}]
        assert screen_graph.screen_landmark_rules("event.trials") == expected_base
        assert screen_graph.screen_verify_rules("event.trials") == expected_base
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_production_screen_verify_yaml_contains_survivor_status_tab_rules() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        for tab in ("status", "details"):
            screen = f"survivor_status.{tab}"
            expected = [
                {
                    "match": "survivor_status.title",
                    "threshold": 0.9,
                    "tab_active": f"survivor_status.{tab}",
                }
            ]
            assert screen_graph.screen_landmark_rules(screen) == expected
            assert screen_graph.screen_verify_rules(screen) == expected
        expected_base = [{"match": "survivor_status.title", "threshold": 0.9}]
        assert screen_graph.screen_landmark_rules("survivor_status") == expected_base
        assert screen_graph.screen_verify_rules("survivor_status") == expected_base
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]


def test_production_screen_verify_yaml_contains_alliance_invitation_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("alliance.invitation")
        rules = screen_graph.screen_verify_rules("alliance.invitation")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "alliance.title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_rewards_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("rewards")
        rules = screen_graph.screen_verify_rules("rewards")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [
        {"match": "rewards.title", "threshold": 0.9},
        {"match": "rewards.title.v2", "threshold": 0.9},
    ]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_increase_level_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("increase_level")
        rules = screen_graph.screen_verify_rules("increase_level")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "increase_level.title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_heroes_sr_new_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("heroes.sr.new")
        rules = screen_graph.screen_verify_rules("heroes.sr.new")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "heroes.sr.new.close", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_frostdragon_tyrant_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("text.frostdragon_tyrant")
        rules = screen_graph.screen_verify_rules("text.frostdragon_tyrant")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "text.frostdragon_tyrant", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_ads_natalia_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("ads.natalia")
        rules = screen_graph.screen_verify_rules("ads.natalia")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "ads.natalia.title", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected


def test_production_screen_verify_yaml_contains_is_new_people_rule() -> None:
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
    try:
        landmarks = screen_graph.screen_landmark_rules("isNewPeople")
        rules = screen_graph.screen_verify_rules("isNewPeople")
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    expected = [{"match": "button.welcome_in", "threshold": 0.9}]
    assert landmarks == expected
    assert rules == expected
