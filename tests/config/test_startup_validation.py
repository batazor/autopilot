from __future__ import annotations

from pathlib import Path

import pytest

from config.paths import repo_root
from config.startup_validation import assert_startup_configs_valid, validate_startup_configs
from scenarios import template_resolver


def _write_edge_taps(root: Path, text: str = "edges: {}\n") -> None:
    (root / "navigation").mkdir()
    (root / "navigation" / "edge_taps.yaml").write_text(text, encoding="utf-8")


def _write_module_overlay(root: Path, module_id: str, overlay_yaml: str) -> Path:
    mod = root / "modules" / "core" / module_id
    (mod / "analyze").mkdir(parents=True)
    (mod / "module.yaml").write_text(
        f"id: {module_id}\ntitle: {module_id}\nwiki: false\n",
        encoding="utf-8",
    )
    path = mod / "analyze" / "analyze.yaml"
    path.write_text(overlay_yaml, encoding="utf-8")
    return path


def _write_empty_module_overlay(root: Path) -> None:
    _write_module_overlay(root, "test", "overlay: []\n")


def _scenario_root(root: Path) -> Path:
    mod = root / "modules" / "core" / "test_scenarios"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        "id: test_scenarios\ntitle: Test scenarios\nwiki: false\n",
        encoding="utf-8",
    )
    scen = mod / "scenarios"
    scen.mkdir(exist_ok=True)
    return scen


def test_startup_validation_reports_missing_analyze_scenario(tmp_path: Path) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"claim_all","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    _write_module_overlay(
        tmp_path,
        "test",
        """
overlay:
  - name: claim_all.visible
    region: claim_all
    action: findIcon
    pushScenario:
      - name: missing_claim_scenario
""".lstrip(),
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:claim_all.visible"
    assert "missing_claim_scenario" in issues[0].message


def test_startup_validation_fails_fast(tmp_path: Path) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    _write_module_overlay(
        tmp_path,
        "test",
        """
overlay:
  - name: broken.visible
    region: missing_region
    action: findIcon
""".lstrip(),
    )

    with pytest.raises(RuntimeError, match="startup config validation failed: 1 issue"):
        assert_startup_configs_valid(tmp_path)


def test_unknown_popup_fallback_scenario_is_resolvable() -> None:
    loaded = template_resolver.load_doc(repo_root(), "dismiss_unknown_popup")

    assert loaded is not None
    _path, doc = loaded
    assert doc.get("enabled") is True
    assert doc.get("device_level") is True


def test_startup_validation_reports_missing_red_dot_capability_on_overlay_rule(
    tmp_path: Path,
) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    _write_module_overlay(
        tmp_path,
        "test",
        """
overlay:
  - name: page.shop.has_red_dot
    region: page.shop
    isRedDot: true
""".lstrip(),
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:page.shop.has_red_dot"
    assert "has_red_dot" in issues[0].message
    assert "page.shop" in issues[0].message


def test_startup_validation_accepts_red_dot_rule_when_capability_enabled(
    tmp_path: Path,
) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.vip","has_red_dot":true,'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    _write_module_overlay(
        tmp_path,
        "test",
        """
overlay:
  - name: page.vip.has_red_dot
    region: page.vip
    isRedDot: true
""".lstrip(),
    )

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_reports_missing_expected_on_text_search_region(
    tmp_path: Path,
) -> None:
    """``match:``/``while_match:`` on a text-action region with a ``_search``
    sibling must carry ``expected:`` — otherwise the overlay engine's
    ``_search`` fallback never activates and popup variants silently exit with
    iterations=0 (as observed on ``tap_tapanywhereyoexit`` where the Patrick
    hero card's prompt rendered 5 % below the Chapter Rewards reference)."""
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (scenario_root / "tap_dismiss.yaml").write_text(
        """
name: dismiss popup
steps:
  - while_match: tapanywhereyoexit
    steps:
      - click: tapanywhereyoexit
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source.startswith("scenario:modules/core/test_scenarios/scenarios/tap_dismiss.yaml")
    assert "tapanywhereyoexit" in issues[0].message
    assert "expected" in issues[0].message
    assert "_search" in issues[0].message


def test_startup_validation_accepts_text_search_region_with_expected(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (scenario_root / "tap_dismiss.yaml").write_text(
        """
name: dismiss popup
steps:
  - while_match: tapanywhereyoexit
    expected: ["tap anywhere"]
    steps:
      - click: tapanywhereyoexit
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert issues == []


def test_startup_validation_accepts_regions_from_module_area_yaml(tmp_path: Path) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (scenario_root.parent / "area.yaml").write_text(
        """
screens:
  - id: 1
    screen_id: test.module
    ocr: references/page.test.png
    regions:
      - name: module.button
        bbox: {x: 1, y: 1, width: 1, height: 1}
""".lstrip(),
        encoding="utf-8",
    )
    (scenario_root / "tap_module.yaml").write_text(
        """
name: tap module
steps:
  - click: module.button
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert issues == []


def test_startup_validation_reports_missing_red_dot_capability_on_dsl_step(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (scenario_root / "check_shop_dot.yaml").write_text(
        """
name: probe shop dot
steps:
  - match: page.shop
    isRedDot: true
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source.startswith(
        "scenario:modules/core/test_scenarios/scenarios/check_shop_dot.yaml"
    )
    assert "has_red_dot" in issues[0].message
    assert "page.shop" in issues[0].message


def test_startup_validation_reports_invalid_ocr_scope(tmp_path: Path) -> None:
    """``validate_dsl_steps`` is the runtime gate for scope typos
    (``ocr`` + ``scope: instnace``). Wiring it into startup means the same
    typo trips before the worker boots, not on the first execute."""
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"some_region",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (scenario_root / "bad_scope.yaml").write_text(
        """
name: bad scope
steps:
  - ocr: some_region
    scope: instnace
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "scenario:modules/core/test_scenarios/scenarios/bad_scope.yaml"
    assert "scope" in issues[0].message
    assert "instnace" in issues[0].message


def test_startup_validation_renders_pointer_template_before_region_checks(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"hand_pointer","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"hand_pointer_small","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"hand_pointer_small_reverse","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (scenario_root / "onboarding.click.{pointer}.yaml").write_text(
        """
name: Onboarding click · ${pointer_name}
steps:
  - while_match: ${pointer}
    max: 1
    steps:
      - click: ${pointer}
""".lstrip(),
        encoding="utf-8",
    )

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_reports_cron_task_without_matching_scenario(
    tmp_path: Path,
) -> None:
    """A cron YAML whose ``task:`` doesn't resolve to any scenario must trip
    at startup — otherwise the scheduler enqueues it every cron tick and the
    worker silently fails it with ``scenario_not_found``."""
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (scenario_root / "by_cron" / "check_arena.yaml").write_text(
        """
name: check arena
cron: "0 */3 * * *"
task: arena_check
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == (
        "cron:modules/core/test_scenarios/scenarios/by_cron/check_arena.yaml"
    )
    assert "arena_check" in issues[0].message


def test_startup_validation_accepts_cron_task_matching_existing_scenario(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (scenario_root / "redeem_gift_codes.yaml").write_text(
        "name: redeem\nsteps: []\n", encoding="utf-8"
    )
    (scenario_root / "by_cron" / "redeem.yaml").write_text(
        """
name: redeem cron
cron: "0 */6 * * *"
task: redeem_gift_codes
""".lstrip(),
        encoding="utf-8",
    )

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_reports_missing_edge_tap_region(tmp_path: Path) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(
        tmp_path,
        """
edges:
  main_city:
    mail: [missing_mail_button]
""".lstrip(),
    )
    _write_empty_module_overlay(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"mail.new","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "edge_taps:main_city->mail"
    assert "missing_mail_button" in issues[0].message
