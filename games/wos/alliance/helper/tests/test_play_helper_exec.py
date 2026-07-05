"""play_helper exec: fresh-frame re-match, chat escape, approval-gated taps."""
from __future__ import annotations

import asyncio

import numpy as np
from games.wos.alliance.helper import exec as helper_exec

from layout.types import Point
from tasks.dsl_exec.context import DslExecContext


class _FakeActions:
    def __init__(self, *, tap_ok: bool = True) -> None:
        self.tap_ok = tap_ok
        self.taps: list[tuple[Point, str]] = []
        self.approval_flags: list[bool] = []

    def capture_screen_bgr(self, instance_id: str):
        return np.zeros((1280, 720, 3), dtype=np.uint8)

    def tap(self, instance_id: str, point: Point, **kwargs) -> bool:
        self.taps.append((point, str(kwargs.get("approval_region") or "")))
        self.approval_flags.append(bool(kwargs.get("require_approval", True)))
        return self.tap_ok


def _ctx() -> DslExecContext:
    return DslExecContext(redis_client=None, player_id="p1", instance_id="bs6")


def _run(monkeypatch, *, actions, chat_scores, help_hit):
    """Run the handler with matching stubbed out; returns ctx.result.

    ``chat_scores`` is consumed per _chat_score call (pre-tap, then post-tap).
    """
    scores = list(chat_scores)
    monkeypatch.setattr(helper_exec, "_POST_TAP_SETTLE_S", 0.0)
    monkeypatch.setattr(helper_exec, "_MATCH_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(helper_exec, "_templates", lambda: {"help": "tpl", "chat_titles": []})
    monkeypatch.setattr(helper_exec, "_chat_score", lambda _frame: scores.pop(0) if scores else 0.0)
    monkeypatch.setattr(
        helper_exec,
        "_match",
        lambda _frame, _tpl, _bbox, _thr: (help_hit, 0.95 if help_hit else 0.4, Point(535, 566)),
    )
    monkeypatch.setattr(helper_exec.dsl_runtime, "bot_actions", lambda: actions)
    ctx = _ctx()
    asyncio.run(helper_exec._exec_play_helper(ctx))
    return ctx.result


def test_chat_escape_taps_back_and_stops(monkeypatch):
    actions = _FakeActions()
    result = _run(monkeypatch, actions=actions, chat_scores=[0.93], help_hit=True)
    assert result["action"] == "chat_escape"
    assert len(actions.taps) == 1
    assert actions.taps[0][1] == "icon.page.back"


def test_vanished_bubble_is_a_noop(monkeypatch):
    # The stale-overlay case that used to mis-tap into chat: bubble gone on the
    # fresh frame → no tap at all.
    actions = _FakeActions()
    result = _run(monkeypatch, actions=actions, chat_scores=[0.1, 0.1], help_hit=False)
    assert result["action"] == "vanished"
    assert actions.taps == []


def test_live_bubble_tapped(monkeypatch):
    actions = _FakeActions()
    result = _run(monkeypatch, actions=actions, chat_scores=[0.1, 0.1], help_hit=True)
    assert result["action"] == "tapped"
    assert [r for _, r in actions.taps] == ["button.alliance.help"]
    # Own-bot taps skip click-approval by design (operator decision).
    assert actions.approval_flags == [False]


def test_post_tap_slip_escapes_chat(monkeypatch):
    # Bubble expired mid-flight: tap landed, chat opened → immediate back-out.
    actions = _FakeActions()
    result = _run(monkeypatch, actions=actions, chat_scores=[0.1, 0.95], help_hit=True)
    assert result["action"] == "tapped_then_chat_escape"
    assert [r for _, r in actions.taps] == ["button.alliance.help", "icon.page.back"]


def test_approval_blocked_tap_reports_and_stops(monkeypatch):
    # Click-approval mode: a declined tap must not fall through to the
    # post-tap escape (nothing was tapped).
    actions = _FakeActions(tap_ok=False)
    result = _run(monkeypatch, actions=actions, chat_scores=[0.1], help_hit=True)
    assert result["action"] == "tap_blocked"
    assert len(actions.taps) == 1
