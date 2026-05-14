from __future__ import annotations

from pathlib import Path

import pytest

from config.startup_validation import assert_startup_configs_valid, validate_startup_configs


def _write_edge_taps(root: Path, text: str = "edges: {}\n") -> None:
    (root / "navigation").mkdir()
    (root / "navigation" / "edge_taps.yaml").write_text(text, encoding="utf-8")


def test_startup_validation_reports_missing_analyze_scenario(tmp_path: Path) -> None:
    (tmp_path / "analyze" / "analyze_pages").mkdir(parents=True)
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"claim_all","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        "include:\n  - analyze_pages/common.yaml\n",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze_pages" / "common.yaml").write_text(
        """
overlay:
  - name: claim_all.visible
    region: claim_all
    action: findIcon
    pushScenario:
      - name: missing_claim_scenario
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:claim_all.visible"
    assert "missing_claim_scenario" in issues[0].message


def test_startup_validation_fails_fast(tmp_path: Path) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        """
overlay:
  - name: broken.visible
    region: missing_region
    action: findIcon
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="startup config validation failed: 1 issue"):
        assert_startup_configs_valid(tmp_path)


def test_startup_validation_reports_missing_red_dot_capability_on_overlay_rule(
    tmp_path: Path,
) -> None:
    (tmp_path / "analyze" / "analyze_pages").mkdir(parents=True)
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        """
overlay:
  - name: page.shop.has_red_dot
    region: page.shop
    isRedDot: true
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "analyze:page.shop.has_red_dot"
    assert "has_red_dot" in issues[0].message
    assert "page.shop" in issues[0].message


def test_startup_validation_accepts_red_dot_rule_when_capability_enabled(
    tmp_path: Path,
) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.vip","has_red_dot":true,'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        """
overlay:
  - name: page.vip.has_red_dot
    region: page.vip
    isRedDot: true
""".lstrip(),
        encoding="utf-8",
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
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        "overlay: []\n", encoding="utf-8"
    )
    (tmp_path / "scenarios" / "tap_dismiss.yaml").write_text(
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
    assert issues[0].source.startswith("scenario:tap_dismiss.yaml")
    assert "tapanywhereyoexit" in issues[0].message
    assert "expected" in issues[0].message
    assert "_search" in issues[0].message


def test_startup_validation_accepts_text_search_region_with_expected(
    tmp_path: Path,
) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"tapanywhereyoexit","action":"text",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}},'
        '{"name":"tapanywhereyoexit_search",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        "overlay: []\n", encoding="utf-8"
    )
    (tmp_path / "scenarios" / "tap_dismiss.yaml").write_text(
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


def test_startup_validation_reports_missing_red_dot_capability_on_dsl_step(
    tmp_path: Path,
) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":['
        '{"name":"page.shop","bbox":{"x":1,"y":1,"width":1,"height":1}}'
        "]}]}",
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text(
        "overlay: []\n", encoding="utf-8"
    )
    (tmp_path / "scenarios" / "check_shop_dot.yaml").write_text(
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
    assert issues[0].source.startswith("scenario:check_shop_dot.yaml")
    assert "has_red_dot" in issues[0].message
    assert "page.shop" in issues[0].message


def test_startup_validation_reports_invalid_ocr_scope(tmp_path: Path) -> None:
    """``validate_dsl_steps`` is the runtime gate for scope typos
    (``ocr`` + ``scope: instnace``). Wiring it into startup means the same
    typo trips before the worker boots, not on the first execute."""
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"some_region",'
        '"bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (tmp_path / "scenarios" / "bad_scope.yaml").write_text(
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
    assert issues[0].source == "scenario:bad_scope.yaml"
    assert "scope" in issues[0].message
    assert "instnace" in issues[0].message


def test_startup_validation_reports_cron_task_without_matching_scenario(
    tmp_path: Path,
) -> None:
    """A cron YAML whose ``task:`` doesn't resolve to any scenario must trip
    at startup — otherwise the scheduler enqueues it every cron tick and the
    worker silently fails it with ``scenario_not_found``."""
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios" / "by_cron").mkdir(parents=True)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (tmp_path / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (tmp_path / "scenarios" / "by_cron" / "check_arena.yaml").write_text(
        """
name: check arena
cron: "0 */3 * * *"
task: arena_check
""".lstrip(),
        encoding="utf-8",
    )

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "cron:by_cron/check_arena.yaml"
    assert "arena_check" in issues[0].message


def test_startup_validation_accepts_cron_task_matching_existing_scenario(
    tmp_path: Path,
) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios" / "by_cron").mkdir(parents=True)
    _write_edge_taps(tmp_path)
    (tmp_path / "area.json").write_text('{"screens":[]}', encoding="utf-8")
    (tmp_path / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")
    (tmp_path / "scenarios" / "redeem_gift_codes.yaml").write_text(
        "name: redeem\nsteps: []\n", encoding="utf-8"
    )
    (tmp_path / "scenarios" / "by_cron" / "redeem.yaml").write_text(
        """
name: redeem cron
cron: "0 */6 * * *"
task: redeem_gift_codes
""".lstrip(),
        encoding="utf-8",
    )

    assert validate_startup_configs(tmp_path) == []


def test_startup_validation_reports_missing_edge_tap_region(tmp_path: Path) -> None:
    (tmp_path / "analyze").mkdir()
    (tmp_path / "scenarios").mkdir()
    _write_edge_taps(
        tmp_path,
        """
edges:
  main_city:
    mail: [missing_mail_button]
""".lstrip(),
    )
    (tmp_path / "area.json").write_text(
        '{"screens":[{"regions":[{"name":"mail.new","bbox":{"x":1,"y":1,"width":1,"height":1}}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "analyze" / "analyze.yaml").write_text("overlay: []\n", encoding="utf-8")

    issues = validate_startup_configs(tmp_path)

    assert len(issues) == 1
    assert issues[0].source == "edge_taps:main_city->mail"
    assert "missing_mail_button" in issues[0].message
