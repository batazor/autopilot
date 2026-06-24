from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from config.paths import repo_root
from config.startup_validation import assert_startup_configs_valid, validate_startup_configs
from dsl import template_resolver

if TYPE_CHECKING:
    from pathlib import Path


def _write_edge_taps(root: Path, text: str = "edges: {}\n", *, game: str = "wos") -> None:
    # Validator now walks per-module ``routes/edge_taps.yaml`` only — no canonical
    # file exists. Stash the fixture under a tiny throwaway module so iter_module_dirs
    # picks it up.
    mod = root / "games" / game / "core" / "_edge_taps_fixture"
    (mod / "routes").mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        "id: _edge_taps_fixture\ntitle: edge taps fixture\nwiki: false\n",
        encoding="utf-8",
    )
    (mod / "routes" / "edge_taps.yaml").write_text(text, encoding="utf-8")


def _write_screen_verify(
    root: Path,
    text: str,
    *,
    game: str = "wos",
    module_id: str = "_screen_verify_fixture",
) -> None:
    mod = root / "games" / game / "core" / module_id
    (mod / "routes").mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        f"id: {module_id}\ntitle: screen verify fixture\nwiki: false\n",
        encoding="utf-8",
    )
    (mod / "routes" / "screen_verify.yaml").write_text(text, encoding="utf-8")


def _write_area_regions(root: Path, area_json_text: str, *, game: str = "wos") -> None:
    """Stash test-fixture area data as a per-module ``area.yaml`` manifest.

    The validator merges all ``modules/**/area.yaml`` via ``load_area_doc``.
    Tests that used to write a root ``area.json`` now seed the same data into a
    throwaway module so the merged view picks it up. Empty docs (no screens) are
    a no-op.
    """
    import json as _json

    import yaml as _yaml

    raw = _json.loads(area_json_text) if area_json_text.strip().startswith("{") else _yaml.safe_load(area_json_text)
    if not isinstance(raw, dict) or not raw.get("screens"):
        return  # nothing to register
    mod = root / "games" / game / "core" / "_area_fixture"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text(
        "id: _area_fixture\ntitle: area fixture\nwiki: false\n",
        encoding="utf-8",
    )
    (mod / "area.yaml").write_text(_yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _write_module_overlay(root: Path, module_id: str, overlay_yaml: str) -> Path:
    mod = root / "games" / "wos" / "core" / module_id
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
    mod = root / "games" / "wos" / "core" / "test_scenarios"
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"claim_all","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
    )
    _write_module_overlay(
        tmp_path,
        "test",
        """
overlay:
  - name: claim_all.visible
    region: claim_all
    action: findIcon
    steps:
      - push_scenario: missing_claim_scenario
""".lstrip(),
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:claim_all.visible"
    assert "missing_claim_scenario" in issues[0].message


def test_startup_validation_raises_on_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default behaviour: issues abort startup so the operator notices.

    No interactive TTY prompt (that would hang the embedded supervisor in a
    background thread); the only override is the ``WOS_VALIDATION_ACK`` env
    var, exercised by the next test.
    """
    monkeypatch.delenv("WOS_VALIDATION_ACK", raising=False)
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
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

    with pytest.raises(RuntimeError, match="1 error"):
        assert_startup_configs_valid(tmp_path)


def test_startup_validation_env_ack_lets_supervisor_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``WOS_VALIDATION_ACK=1`` is the non-interactive override — it lets the
    operator boot through known issues without an interactive prompt that
    would otherwise hang the embedded supervisor's background thread.
    """
    import logging as _logging

    monkeypatch.setenv("WOS_VALIDATION_ACK", "1")
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
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

    with caplog.at_level(_logging.WARNING, logger="config.startup_validation"):
        assert_startup_configs_valid(tmp_path)

    assert any(
        "acknowledged via WOS_VALIDATION_ACK" in rec.getMessage()
        for rec in caplog.records
    ), caplog.text


def test_startup_validation_screen_family_gap_is_warning_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WOS_VALIDATION_ACK", raising=False)
    _scenario_root(tmp_path)
    _write_edge_taps(
        tmp_path,
        """
edges:
  shop:
    shop.daily_deals: [shop.tabs_strip]
  shop.daily_deals:
    shop: [shop.tabs_strip]
  shop.get_gems:
    shop: [shop.tabs_strip]
""".lstrip(),
    )
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"shop.tabs_strip",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
    )
    _write_screen_verify(
        tmp_path,
        """
families:
  shop:
    hub: shop
    prefix: shop.
    tab_region: shop.tabs_strip
screens:
  shop:
    rules: [{match: shop.tabs_strip}]
  shop.daily_deals:
    rules: [{match: shop.tabs_strip}]
  shop.get_gems:
    rules: [{match: shop.tabs_strip}]
""".lstrip(),
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].source == "screen_family:shop"
    assert_startup_configs_valid(tmp_path)


def test_startup_validation_credits_via_main_city_family_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family member reachable from the hub only by bouncing through
    ``main_city`` is NOT flagged as a route gap.

    Shop documents this exact pattern ("cross-tab navigation goes
    main_city → shop → tab") and many Deals are entered straight from
    main_city, so the universal hub counts as a routing waypoint. Here
    ``shop → shop.get_gems`` exists only as ``shop → main_city → shop.get_gems``.
    """
    monkeypatch.delenv("WOS_VALIDATION_ACK", raising=False)
    _scenario_root(tmp_path)
    _write_edge_taps(
        tmp_path,
        """
edges:
  shop:
    shop.daily_deals: [shop.tabs_strip]
    main_city: [shop.tabs_strip]
  main_city:
    shop.get_gems: [shop.tabs_strip]
  shop.daily_deals:
    shop: [shop.tabs_strip]
  shop.get_gems:
    shop: [shop.tabs_strip]
""".lstrip(),
    )
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"shop.tabs_strip",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
    )
    _write_screen_verify(
        tmp_path,
        """
families:
  shop:
    hub: shop
    prefix: shop.
    tab_region: shop.tabs_strip
screens:
  shop:
    rules: [{match: shop.tabs_strip}]
  shop.daily_deals:
    rules: [{match: shop.tabs_strip}]
  shop.get_gems:
    rules: [{match: shop.tabs_strip}]
""".lstrip(),
    )

    issues = validate_startup_configs(tmp_path)

    family_gaps = [i for i in issues if i.source.startswith("screen_family:")]
    assert family_gaps == [], family_gaps


def test_unknown_popup_fallback_scenario_is_resolvable() -> None:
    loaded = template_resolver.load_doc(repo_root(), "dismiss_unknown_popup")

    assert loaded is not None
    _path, doc = loaded
    assert doc.get("enabled") is True
    assert doc.get("device_level") is True


def test_unknown_popup_prefers_labeled_tap_anywhere_before_smart_detector() -> None:
    loaded = template_resolver.load_doc(repo_root(), "dismiss_unknown_popup")

    assert loaded is not None
    _path, doc = loaded
    steps = doc.get("steps") or []
    flattened = str(steps)

    tap_idx = flattened.find("button.tap_anywhere_to_exit")
    exec_idx = flattened.find("'exec': 'dismiss_popup'")

    assert tap_idx >= 0
    assert exec_idx >= 0
    assert tap_idx < exec_idx


def test_startup_validation_reports_missing_red_dot_capability_on_overlay_rule(
    tmp_path: Path,
) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"page.vip","has_red_dot":true,'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
    assert issues[0].source.startswith("scenario:games/wos/core/test_scenarios/scenarios/tap_dismiss.yaml")
    assert "tapanywhereyoexit" in issues[0].message
    assert "expected" in issues[0].message
    assert "_search" in issues[0].message


def test_startup_validation_accepts_text_search_region_with_expected(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
        "scenario:games/wos/core/test_scenarios/scenarios/check_shop_dot.yaml"
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"some_region",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
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
    assert issues[0].source == "scenario:games/wos/core/test_scenarios/scenarios/bad_scope.yaml"
    assert "scope" in issues[0].message
    assert "instnace" in issues[0].message


def test_startup_validation_renders_pointer_template_before_region_checks(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"hand_pointer","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"hand_pointer_small","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"hand_pointer_small_reverse","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"hand_pointer_build","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
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
        "cron:games/wos/core/test_scenarios/scenarios/by_cron/check_arena.yaml"
    )
    assert "arena_check" in issues[0].message


def test_startup_validation_accepts_cron_task_matching_existing_scenario(
    tmp_path: Path,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "by_cron").mkdir(parents=True)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)
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
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"mail.new","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "edge_taps:main_city->mail"
    assert "missing_mail_button" in issues[0].message


def test_startup_validation_checks_edge_taps_against_their_game_area_regions(
    tmp_path: Path,
) -> None:
    _write_edge_taps(
        tmp_path,
        """
edges:
  main_city:
    conquest: [main_city.to.conquest]
""".lstrip(),
        game="kingshot",
    )
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"main_city.to.conquest","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        game="kingshot",
    )

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_accepts_system_back_edge_tap_action(tmp_path: Path) -> None:
    _scenario_root(tmp_path)
    _write_edge_taps(
        tmp_path,
        """
edges:
  rewards:
    main_city:
      - type: system_back
""".lstrip(),
    )
    _write_empty_module_overlay(tmp_path)

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_reports_module_overlay_region_missing_from_runtime_area(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: overlay rules must resolve in the merged module area doc.

    The runtime overlay loader (``default_area_doc_for_overlay``) is patched to
    return an empty doc, simulating a regression where some modules' regions
    fail to land in the runtime view. Validation must flag the orphan region.
    """
    _scenario_root(tmp_path)
    _write_edge_taps(tmp_path)
    _write_empty_module_overlay(tmp_path)

    ads = _modules_root_for(_default_game(), repo_root=tmp_path) / "ads"
    (ads / "analyze").mkdir(parents=True)
    (ads / "module.yaml").write_text("id: ads\ntitle: ads\nwiki: false\n", encoding="utf-8")
    (ads / "analyze" / "analyze.yaml").write_text(
        """
overlay:
  - name: pop.visible
    region: pop.title
    action: findIcon
""".lstrip(),
        encoding="utf-8",
    )
    (ads / "area.yaml").write_text(
        """
screens:
  - screen_id: pop
    regions:
      - name: pop.title
        bbox: {x: 1, y: 1, width: 1, height: 1}
""".lstrip(),
        encoding="utf-8",
    )

    def _empty_runtime_area_doc(root: Path) -> dict:
        _ = root
        return {"screens": []}

    monkeypatch.setattr(
        "analysis.overlay_area.default_area_doc_for_overlay",
        _empty_runtime_area_doc,
    )

    issues = validate_startup_configs(tmp_path)

    assert any(
        i.source == "analyze:pop.visible"
        and "pop.title" in i.message
        and "games/wos/*/area.yaml" in i.message
        for i in issues
    )


def test_startup_validation_accepts_ads_myriad_region_on_real_repo() -> None:
    issues = validate_startup_configs(repo_root())
    myriad_issues = [
        i
        for i in issues
        if i.source == "analyze:myriad_bazaar.visible"
        or "myriad_bazaar.title" in i.message
    ]
    assert myriad_issues == []


def test_edge_taps_accepts_any_of_with_known_regions(tmp_path: Path) -> None:
    # ``any_of`` is a real navigator tap action (one destination reachable by
    # several buttons — navigator._tap_any_of_async); the validator must accept
    # it, not reject it as an unknown action type.
    _scenario_root(tmp_path)
    _write_empty_module_overlay(tmp_path)
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":['
        '{"name":"play.free","bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"play.frosty","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
    )
    _write_edge_taps(
        tmp_path,
        "edges:\n"
        "  hub:\n"
        "    gameplay:\n"
        "      - type: any_of\n"
        "        regions: [play.free, play.frosty]\n",
    )
    issues = validate_startup_configs(tmp_path)
    assert [i for i in issues if i.source.startswith("edge_taps")] == []


def test_edge_taps_any_of_flags_unknown_region(tmp_path: Path) -> None:
    _scenario_root(tmp_path)
    _write_empty_module_overlay(tmp_path)
    _write_area_regions(
        tmp_path,
        '{"screens":[{"regions":[{"name":"play.free","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
    )
    _write_edge_taps(
        tmp_path,
        "edges:\n"
        "  hub:\n"
        "    gameplay:\n"
        "      - type: any_of\n"
        "        regions: [play.free, play.ghost]\n",
    )
    issues = validate_startup_configs(tmp_path)
    assert any(
        "any_of region" in i.message and "play.ghost" in i.message for i in issues
    )
