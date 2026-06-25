"""Overlay rule ``steps:`` extraction + worker-side execution.

Covers:
  * ``optional_inline_steps`` filters ``push_scenario`` (handled elsewhere) and
    drops step shapes the worker doesn't execute.
  * ``compile_overlay_rule`` populates the process-local registry so the
    worker can look up ``steps:`` by rule name.
  * ``_execute_inline_overlay_steps`` taps the right pixel for the rule's own
    region using the payload's match coords, with cond gating and rule-region
    safety.
"""
from __future__ import annotations

import asyncio
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from analysis.overlay_compile import (
    _reset_inline_steps_registry,
    compile_overlay_rule,
    get_inline_steps,
)
from analysis.overlay_rules import optional_inline_steps


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    _reset_inline_steps_registry()
    yield
    _reset_inline_steps_registry()


def test_optional_inline_steps_extracts_click_and_wait() -> None:
    rule = {
        "name": "x",
        "steps": [
            {"click": "foo.button"},
            {"wait": "500ms"},
        ],
    }
    out = optional_inline_steps(rule)
    assert out == [{"click": "foo.button"}, {"wait": "500ms"}]


def test_optional_inline_steps_skips_push_scenario() -> None:
    rule = {
        "name": "x",
        "steps": [
            {"push_scenario": "claim_mail"},
            {"click": "foo.button"},
        ],
    }
    assert optional_inline_steps(rule) == [{"click": "foo.button"}]


def test_optional_inline_steps_drops_unsupported_step_types() -> None:
    # ``match`` / ``while_match`` / ``ocr`` / unknown keys → silently filtered.
    rule = {
        "name": "x",
        "steps": [
            {"match": "foo"},
            {"while_match": "bar"},
            {"ocr": "baz"},
            {"frobnicate": True},
            {"click": "ok.button"},
        ],
    }
    assert optional_inline_steps(rule) == [{"click": "ok.button"}]


def test_optional_inline_steps_preserves_cond_guard() -> None:
    rule = {
        "name": "x",
        "steps": [
            {"click": "foo.button", "cond": "active_player != null"},
        ],
    }
    out = optional_inline_steps(rule)
    assert out == [{"click": "foo.button", "cond": "active_player != null"}]


def test_compile_overlay_rule_registers_inline_steps() -> None:
    rule = {
        "name": "vault.box.has_red_dot",
        "region": "vault.box",
        "isRedDot": True,
        "screens": ["deals.vault"],
        "steps": [{"click": "vault.box"}],
    }
    compiled = compile_overlay_rule(rule)
    assert compiled is not None
    assert compiled.inline_steps == ({"click": "vault.box"},)
    assert get_inline_steps("vault.box.has_red_dot") == ({"click": "vault.box"},)


def test_compile_overlay_rule_clears_registry_when_no_inline_steps() -> None:
    # Compile once with inline steps, then again without → registry is dropped.
    compile_overlay_rule(
        {"name": "r", "region": "x", "steps": [{"click": "x"}]}
    )
    assert get_inline_steps("r") == ({"click": "x"},)
    compile_overlay_rule(
        {"name": "r", "region": "x", "steps": [{"push_scenario": "scen"}]}
    )
    assert get_inline_steps("r") == ()


# --- worker execution path ------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}

    async def hget(self, key: str, field: str) -> Any:
        return self._state.get(key, {}).get(field)


def _make_worker_with_steps(
    rule_name: str, steps: list[dict[str, Any]], region: str
) -> tuple[Any, MagicMock]:
    from worker.instance_worker_overlay import InstanceWorkerOverlayMixin

    compile_overlay_rule(
        {"name": rule_name, "region": region, "steps": steps}
    )

    actions = MagicMock()
    actions.screen_resolution.return_value = (720, 1280)
    actions.tap.return_value = True

    async def _no_focus() -> str:
        # Collaborator the overlay mixin expects from the full worker host
        # (instance_worker_redis). "" = focus mode off, so inline steps run.
        return ""

    # Plain instance with the mixin's attrs stubbed — we only call one method.
    obj = types.SimpleNamespace(
        _cfg=types.SimpleNamespace(instance_id="inst-1"),
        _redis=_FakeRedis(),
        _bot_actions=actions,
        _focus_scenario=_no_focus,
    )
    # Bind the method we want to exercise to the namespace.
    obj._execute_inline_overlay_steps = (
        InstanceWorkerOverlayMixin._execute_inline_overlay_steps.__get__(obj)
    )
    return obj, actions


def test_inline_click_taps_match_center_pct() -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x", [{"click": "vault.box"}], region="vault.box"
    )
    payload = {
        "region": "vault.box",
        "tap_match_x_pct": 50.0,
        "tap_match_y_pct": 25.0,
    }
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", payload))
    actions.tap.assert_called_once()
    args, kwargs = actions.tap.call_args
    assert args[0] == "inst-1"
    pt = args[1]
    # 50% × 720 = 360, 25% × 1280 = 320
    assert (pt.x, pt.y) == (360, 320)
    assert kwargs.get("approval_region") == "vault.box"


def test_inline_click_falls_back_to_bbox_center() -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x", [{"click": "vault.box"}], region="vault.box"
    )
    # ``tap_match_*_pct`` missing → fall back to ``tap_*_pct`` (bbox center).
    payload = {
        "region": "vault.box",
        "tap_x_pct": 10.0,
        "tap_y_pct": 10.0,
    }
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", payload))
    actions.tap.assert_called_once()
    pt = actions.tap.call_args[0][1]
    assert (pt.x, pt.y) == (72, 128)


def test_inline_click_rejects_different_region() -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x", [{"click": "other.region"}], region="vault.box"
    )
    payload = {
        "region": "vault.box",
        "tap_match_x_pct": 50.0,
        "tap_match_y_pct": 25.0,
    }
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", payload))
    actions.tap.assert_not_called()


def test_inline_click_respects_cond_gate() -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x",
        [{"click": "vault.box", "cond": "active_player != null"}],
        region="vault.box",
    )
    # No active_player in Redis → cond is False → click skipped.
    payload = {
        "region": "vault.box",
        "tap_match_x_pct": 50.0,
        "tap_match_y_pct": 25.0,
    }
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", payload))
    actions.tap.assert_not_called()


def test_inline_wait_sleeps_then_click(monkeypatch: pytest.MonkeyPatch) -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x",
        [{"wait": "100ms"}, {"click": "vault.box"}],
        region="vault.box",
    )
    sleeps: list[float] = []

    async def _fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    monkeypatch.setattr("worker.instance_worker_overlay.asyncio.sleep", _fake_sleep)

    payload = {
        "region": "vault.box",
        "tap_match_x_pct": 50.0,
        "tap_match_y_pct": 25.0,
    }
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", payload))
    assert sleeps == [0.1]
    actions.tap.assert_called_once()


def test_no_inline_steps_is_noop() -> None:
    obj, actions = _make_worker_with_steps(
        "rule.x", [{"push_scenario": "scen"}], region="vault.box"
    )
    # Only push_scenario → no inline steps registered → no tap.
    asyncio.run(obj._execute_inline_overlay_steps("rule.x", {"region": "vault.box"}))
    actions.tap.assert_not_called()
