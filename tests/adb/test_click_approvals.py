from __future__ import annotations

import json
import time
from typing import Any

import pytest

import adb.approvals as tap

pytestmark = pytest.mark.integration


class _RedisProxy:
    """Proxy around real redis client for approval tests.

    Keeps test intent from the old FakeRedis helpers, but uses Redis as storage.
    """

    def __init__(
        self,
        client: Any,
        *,
        approve_on_current: bool = False,
        decision_on_current: str | None = None,
        drop_current_after_publish: bool = False,
        drop_current_without_response_once: bool = False,
        inject_foreign_current: bool = False,
    ) -> None:
        self._r = client
        self._approve_on_current = approve_on_current
        self._decision_on_current = decision_on_current
        self._drop_current_after_publish = drop_current_after_publish
        self._drop_current_without_response_once = drop_current_without_response_once
        self._inject_foreign_current = inject_foreign_current

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        ok = bool(self._r.set(key, value, ex=ex, nx=nx))
        if not ok:
            return False

        if ":current:" in key:
            if self._drop_current_without_response_once:
                self._r.delete(key)
                self._drop_current_without_response_once = False
                return True

            if self._inject_foreign_current:
                # Hijack the current slot with a foreign request_id.
                cur = json.loads(value)
                cur["request_id"] = "adb:bs1:foreign"
                cur["response_key"] = "wos:ui:click_approval:response:adb:bs1:foreign"
                self._r.set(key, json.dumps(cur), ex=ex)
                self._inject_foreign_current = False
                return True

            if self._decision_on_current is not None:
                payload = json.loads(value)
                self._r.set(str(payload["response_key"]), self._decision_on_current)
            elif self._approve_on_current:
                payload = json.loads(value)
                self._r.set(str(payload["response_key"]), "approve")
            if self._drop_current_after_publish:
                self._r.delete(key)
        return True

    def delete(self, key: str) -> int:
        return int(self._r.delete(key))

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._r.expire(key, ttl))

    def lpush(self, key: str, value: str) -> int:
        return int(self._r.lpush(key, value))

    def ltrim(self, key: str, start: int, end: int) -> bool:
        return bool(self._r.ltrim(key, start, end))

    def hgetall(self, key: str) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self._r.hgetall(key) or {}).items()}

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        return int(self._r.hset(key, mapping=mapping))


def _patch_redis(monkeypatch: Any, client: Any) -> None:
    monkeypatch.setattr(tap, "_redis", lambda: client)
    monkeypatch.setattr(tap, "_APPROVAL_POLL_SECONDS", 0.0)
    monkeypatch.setattr(tap, "_APPROVAL_PUBLISH_WAIT_SECONDS", 0.01)


def test_require_approval_default_on_when_redis_key_missing(
    monkeypatch: Any,
    redis_sync: Any,
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None


def test_require_approval_explicit_off_bypasses(
    monkeypatch: Any,
    redis_sync: Any,
) -> None:
    r = _RedisProxy(redis_sync)
    r.set("wos:ui:click_approval:enabled:bs1", "0")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is None


def test_require_approval_uses_request_specific_response(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None
    assert r.get(f"wos:ui:click_approval:response:{req_id}") == "approve"
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id
    assert current["status"] == "approved"


def test_require_approval_does_not_reuse_existing_pending_request(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.set(
        "wos:ui:click_approval:current:bs1",
        json.dumps(
            {
                "request_id": "adb:bs1:old",
                "response_key": "wos:ui:click_approval:response:adb:bs1:old",
                "status": "waiting",
            }
        ),
    )
    r.set("wos:ui:click_approval:response:adb:bs1:old", "approve")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 10, "y": 20})

    assert ok is False
    assert req_id is None
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == "adb:bs1:old"


def test_require_approval_reaps_stale_pending_request_from_previous_scenario(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_region": "ads_rookie_value_pack",
            "current_scenario": "ads_rookie_value_pack",
        },
    )
    r.set(
        "wos:ui:click_approval:current:bs1",
        json.dumps(
            {
                "request_id": "adb:bs1:old",
                "response_key": "wos:ui:click_approval:response:adb:bs1:old",
                "status": "waiting",
                "created_at": 1,
                "context": {"scenario": "who_i_am"},
            }
        ),
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 10, "y": 20})

    assert ok is True
    assert req_id is not None
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id
    assert current["context"]["scenario"] == "ads_rookie_value_pack"


def test_require_approval_reaps_orphan_pending_with_empty_old_scenario(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """A pending approval with empty ``context.scenario`` is an orphan from a
    previous worker session (or pre-DSL publisher) — when a new task tries to
    publish, the orphan must be reaped, not preserved as "unknown owner".

    Regression: pre-fix, ``_clear_stale_approval_current`` early-returned on
    empty ``old_scenario`` and the orphan blocked the next worker forever.
    """
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={"current_scenario": "tap_reconnect_button"},
    )
    r.set(
        "wos:ui:click_approval:current:bs1",
        json.dumps(
            {
                "request_id": "adb:bs1:orphan",
                "response_key": "wos:ui:click_approval:response:adb:bs1:orphan",
                "status": "waiting",
                "created_at": 1,  # ancient → past the stale threshold
                "context": {},  # no scenario field — the orphan signature
            }
        ),
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 10, "y": 20})

    assert ok is True
    assert req_id is not None
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id  # new request, not the orphan
    assert current["context"]["scenario"] == "tap_reconnect_button"


def test_clear_stale_approval_preserves_known_owner_when_new_scenario_empty(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """Inverse of the orphan-reap path: if the NEW publisher can't identify
    itself (``new_context.scenario`` is empty), we must NOT clobber the
    existing known-owner approval. Tested directly on the helper to isolate
    the polarity check from ``_require_approval``'s broader publish flow."""
    r = _RedisProxy(redis_sync)
    r.set(
        "wos:ui:click_approval:current:bs1",
        json.dumps(
            {
                "request_id": "adb:bs1:known_owner",
                "response_key": "wos:ui:click_approval:response:adb:bs1:known_owner",
                "status": "waiting",
                "created_at": 1,  # ancient
                "context": {"scenario": "ads_rookie_value_pack"},
            }
        ),
    )
    _patch_redis(monkeypatch, r)

    tap._clear_stale_approval_current(
        instance_id="bs1",
        current_key="wos:ui:click_approval:current:bs1",
        new_context={},  # empty new_scenario → can't claim
    )

    # Original known-owner approval survives unchanged.
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == "adb:bs1:known_owner"
    assert current["context"]["scenario"] == "ads_rookie_value_pack"


def test_clear_stale_approval_preserves_same_scenario_resume(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """Both old and new identify as the same scenario → cooperative-preempt
    resume case. Don't clobber: the task is re-publishing its OWN approval."""
    r = _RedisProxy(redis_sync)
    r.set(
        "wos:ui:click_approval:current:bs1",
        json.dumps(
            {
                "request_id": "adb:bs1:resume",
                "response_key": "wos:ui:click_approval:response:adb:bs1:resume",
                "status": "waiting",
                "created_at": 1,
                "context": {"scenario": "ads_rookie_value_pack"},
            }
        ),
    )
    _patch_redis(monkeypatch, r)

    tap._clear_stale_approval_current(
        instance_id="bs1",
        current_key="wos:ui:click_approval:current:bs1",
        new_context={"scenario": "ads_rookie_value_pack"},
    )

    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == "adb:bs1:resume"


def test_approval_owner_check_detects_task_change(monkeypatch: Any, redis_sync: Any) -> None:
    r = _RedisProxy(redis_sync)
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_id": "task:new",
            "current_scenario": "new_scenario",
        },
    )
    _patch_redis(monkeypatch, r)

    assert (
        tap._approval_owner_still_current(
            "bs1",
            {
                "current_task_id": "task:old",
                "scenario": "old_scenario",
            },
        )
        is False
    )


def test_require_approval_set_node_drops_stale_task_region(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "",
            "current_task_player": "765502864",
            "current_task_region": "ads_rookie_value_pack",
            "current_scenario": "ads_rookie_value_pack",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval(
        "bs1",
        {"type": "set_node", "set_node": "main_city"},
    )

    assert ok is True
    assert req_id is not None
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["type"] == "set_node"
    # ``current_task_region`` is dropped because empty for ``set_node`` —
    # the publisher omits empty-string fields. UI reads via ``.get(k) or ""``
    # so absence is indistinguishable from empty.
    assert current["context"].get("current_task_region", "") == ""
    assert current["context"]["current_task_player"] == "765502864"
    assert current["context"]["scenario"] == "ads_rookie_value_pack"


def test_require_approval_navigation_drops_stale_task_region(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "chief_profile",
            "current_task_region": "chapter",
            "current_scenario": "chapter_task_router",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval(
        "bs1",
        {
            "type": "tap",
            "x": 1,
            "y": 2,
            "region": "back_button",
            "approval_source": "navigation",
            "approval_context": {
                "from_screen": "chief_profile",
                "to_screen": "main_city",
            },
        },
    )

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    # ``current_task_region`` is dropped because empty for navigation taps.
    assert current["context"].get("current_task_region", "") == ""
    assert current["context"]["approval_source"] == "navigation"
    assert current["context"]["approval_from_screen"] == "chief_profile"
    assert current["context"]["approval_to_screen"] == "main_city"


def test_require_approval_tap_keeps_task_region(monkeypatch: Any, redis_sync: Any) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset("wos:instance:bs1:state", mapping={"current_task_region": "ads_rookie_value_pack"})
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_region"] == "ads_rookie_value_pack"


def test_require_approval_context_strips_empty_fields(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """The published ``context`` must not carry the 23+ ``dsl_last_*`` audit
    keys when the corresponding Redis hash fields are absent. UI consumers
    read every field via ``ctx.get(k) or ""`` so an absent key behaves the
    same as ``""`` — and keeping them in the JSON wastes ~1 KB per request
    for noise. ``"0"`` and other falsy-but-meaningful strings must survive.
    """
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "main_city",
            "current_task_player": "765502864",
            "current_task_text": "KSA]sgSc",
            "current_task_confidence": "0.7257",
            # Real falsy value — must NOT be filtered out.
            "current_task_patch_bright_ratio": "0",
            "current_scenario": "mail.claim",
            # All ``dsl_last_*`` keys are intentionally absent.
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval(
        "bs1",
        {
            "type": "tap",
            "x": 1,
            "y": 2,
            "region": "mail.new",
            "approval_source": "navigation",
            "approval_context": {"from_screen": "main_city", "to_screen": "mail"},
        },
    )
    assert ok is True
    ctx = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")["context"]

    # Populated fields survive.
    assert ctx["current_screen"] == "main_city"
    assert ctx["current_task_player"] == "765502864"
    assert ctx["current_task_text"] == "KSA]sgSc"
    assert ctx["current_task_confidence"] == "0.7257"
    assert ctx["scenario"] == "mail.claim"
    # ``"0"`` is a meaningful value — must NOT be stripped.
    assert ctx["current_task_patch_bright_ratio"] == "0"
    # Mirrored approval fields (consumers read these from ctx, not top-level).
    assert ctx["approval_source"] == "navigation"
    assert ctx["approval_from_screen"] == "main_city"
    assert ctx["approval_to_screen"] == "mail"
    assert ctx["approval_region"] == "mail.new"

    # All these were empty in Redis → must be absent from the published ctx.
    omitted = [
        "current_task_region",
        "current_task_threshold",
        "current_task_score",
        "current_task_template_bright_ratio",
        "current_task_match_top_left_x",
        "current_task_match_top_left_y",
        "current_task_template_w",
        "current_task_template_h",
        "current_task_tap_match_x_pct",
        "current_task_tap_match_y_pct",
        "dsl_last_match_region",
        "dsl_last_match_threshold",
        "dsl_last_match_score",
        "dsl_last_match_matched",
        "dsl_last_match_detail",
        "dsl_last_match_at",
        "dsl_last_match_top_left_x",
        "dsl_last_match_top_left_y",
        "dsl_last_match_template_w",
        "dsl_last_match_template_h",
        "dsl_last_match_search_region",
        "dsl_last_match_tap_x_pct",
        "dsl_last_match_tap_y_pct",
        "dsl_last_match_tap_match_x_pct",
        "dsl_last_match_tap_match_y_pct",
        "dsl_last_ocr_region",
        "dsl_last_ocr_store",
        "dsl_last_ocr_status",
        "dsl_last_ocr_threshold",
        "dsl_last_ocr_confidence",
        "dsl_last_ocr_raw_text",
        "dsl_last_ocr_value",
        "dsl_last_ocr_at",
    ]
    leaked = [k for k in omitted if k in ctx]
    assert leaked == [], f"empty fields leaked into context: {leaked}"


def test_require_approval_tap_uses_dsl_last_match_position_context(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_region": "page.worker.add",
            "dsl_last_match_region": "page.worker.add",
            "dsl_last_match_top_left_x": "612",
            "dsl_last_match_top_left_y": "752",
            "dsl_last_match_template_w": "40",
            "dsl_last_match_template_h": "36",
            "dsl_last_match_tap_match_x_pct": "87.8",
            "dsl_last_match_tap_match_y_pct": "60.1",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    ctx = current["context"]
    assert ctx["current_task_match_top_left_x"] == "612"
    assert ctx["current_task_match_top_left_y"] == "752"
    assert ctx["current_task_template_w"] == "40"
    assert ctx["current_task_template_h"] == "36"


def test_require_approval_tap_includes_threshold_and_score_context(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_region": "hand_pointer",
            "current_task_threshold": "0.92",
            "current_task_score": "0.9553",
            "current_scenario": "hand_pointer",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_threshold"] == "0.92"
    assert current["context"]["current_task_score"] == "0.9553"


def test_require_approval_tap_falls_back_to_last_overlay_hints(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """Overlay-hint fallback fires only when ``last_overlay_match_region``
    matches the current task's region — otherwise we'd splice in stale text
    from an unrelated rule (the bug that surfaced on ``tap_reconnect_button``
    showing "Appoint Survivor..." text from an earlier ``chapter.task``
    overlay cycle)."""
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_region": "chapter.task",
            "current_task_threshold": "",
            "current_task_score": "",
            "last_overlay_match_region": "chapter.task",
            "last_overlay_match_threshold": "0.92",
            "last_overlay_match_score": "0.951",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval(
        "bs1",
        {"type": "tap", "x": 1, "y": 2, "region": "chapter.task"},
    )

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_threshold"] == "0.92"
    assert current["context"]["current_task_score"] == "0.951"


def test_require_approval_tap_does_not_borrow_overlay_hints_from_other_region(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """The fix: when the last overlay touched a DIFFERENT region than the
    current task, its hints must not bleed into the current approval. This
    is exactly the ``tap_reconnect_button`` case — operator was seeing
    ``"Appoint 3 Survivor(s) to Iron Mine"`` (from a stale ``chapter.task``
    overlay) attributed to the reconnect tap."""
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_region": "reconnect_button",
            "current_task_threshold": "",
            "current_task_text": "",
            "last_overlay_match_region": "chapter.task",
            "last_overlay_match_threshold": "0.92",
            "last_overlay_text": "Appoint 3 Survivor(s) to Iron Mine work role",
            "last_overlay_confidence": "0.9977",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval(
        "bs1",
        {"type": "tap", "x": 1, "y": 2, "region": "reconnect_button"},
    )

    assert ok is True
    ctx = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")["context"]
    # The stale ``Appoint Survivor`` text from the prior overlay cycle must
    # NOT leak into this task's context. Empty-field stripping (see
    # ``test_require_approval_context_strips_empty_fields``) means those
    # keys are absent rather than empty strings.
    assert "current_task_text" not in ctx
    assert "current_task_threshold" not in ctx
    assert "current_task_confidence" not in ctx
    # ``approval_region`` still surfaces the actual task region for the UI.
    assert ctx["approval_region"] == "reconnect_button"


def test_require_approval_aborts_on_foreign_request(monkeypatch: Any, redis_sync: Any) -> None:
    r = _RedisProxy(redis_sync, inject_foreign_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is not None
    state = r.hgetall("wos:instance:bs1:state")
    assert state["last_approval_block_reason"] == "foreign_request"
    assert state["last_approval_request_id"] == req_id
    event = json.loads(redis_sync.lrange("wos:debug:timeline:bs1", 0, 0)[0])
    assert event["event"] == "approval.blocked"
    assert event["reason"] == "foreign_request"
    assert event["request_id"] == req_id


def test_require_approval_republishes_when_current_request_disappears(
    monkeypatch: Any, redis_sync: Any
) -> None:
    r = _RedisProxy(
        redis_sync,
        approve_on_current=True,
        drop_current_without_response_once=True,
    )
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id
    assert current["status"] == "approved"
    events = [
        json.loads(row)
        for row in redis_sync.lrange("wos:debug:timeline:bs1", 0, 10)
    ]
    assert any(e["event"] == "approval.republished" for e in events)


def test_require_approval_calls_preview_capturer_before_publish(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """Caller-attached ``_preview_capturer`` must fire right before the SET so
    the published payload carries a screenshot from the actual decision moment,
    not from the pre-publish cache that may be many seconds old when the slot
    is contended. The callback gets the payload dict and is responsible for
    mutating ``preview_png_rel`` / ``preview_captured_at``."""
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    fired = {"n": 0}

    def _capture(payload: dict[str, Any]) -> None:
        fired["n"] += 1
        payload["preview_png_rel"] = "temporal/fresh.png"
        payload["preview_captured_at"] = 9999.0

    ok, _req_id = tap._require_approval(
        "bs1",
        {
            "type": "tap",
            "x": 1,
            "y": 2,
            "preview_png_rel": "temporal/stale.png",
            "preview_captured_at": 1.0,
            "_preview_capturer": _capture,
        },
    )

    assert ok is True
    assert fired["n"] >= 1, "capturer must fire at least once before publish"
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["preview_png_rel"] == "temporal/fresh.png"
    assert current["preview_captured_at"] == 9999.0
    # The private callback must NOT leak into the serialised Redis payload.
    assert "_preview_capturer" not in current


def test_require_approval_survives_capturer_exception(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """If the preview-refresh callable raises (e.g. ADB hiccup during the
    publish wait), the approval flow still publishes — we'd rather have a
    slightly stale preview than no approval at all."""
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    def _explode(_payload: dict[str, Any]) -> None:
        msg = "ADB unreachable"
        raise RuntimeError(msg)

    ok, _req_id = tap._require_approval(
        "bs1",
        {
            "type": "tap",
            "x": 1,
            "y": 2,
            "preview_png_rel": "temporal/cached.png",
            "preview_captured_at": 1.0,
            "_preview_capturer": _explode,
        },
    )

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    # Falls back to the originally-cached preview from the caller.
    assert current["preview_png_rel"] == "temporal/cached.png"


def test_require_approval_skip_returns_ok_and_queues_consume_marker(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """Operator ``skip`` returns ``ok=True`` (callers must not abort) AND queues
    a one-shot consume marker so the ADB action (tap/swipe/type_text) can be
    bypassed without aborting the scenario.
    """
    r = _RedisProxy(redis_sync, decision_on_current="skip")
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)
    tap._skipped_req_ids.clear()

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None
    assert tap._consume_skip(req_id) is True
    # Marker is single-shot — a second consume must return False.
    assert tap._consume_skip(req_id) is False
    # The slot was cleared by the cleanup path (skip is non-approve).
    assert r.get("wos:ui:click_approval:current:bs1") is None


def test_abort_pending_approval_round_trip(monkeypatch: Any, redis_sync: Any) -> None:
    """``abort_pending_approval`` stamps a reason that ``_approval_abort_reason``
    reads back for any request that started before the abort."""
    r = _RedisProxy(redis_sync)
    _patch_redis(monkeypatch, r)

    tap.abort_pending_approval("bs1", "game restart triggered")

    # A request that entered at t=0 (before the abort) sees the reason.
    assert tap._approval_abort_reason("bs1", since=0.0) == "game restart triggered"
    # A request that started "in the future" (after the abort) is unaffected.
    assert tap._approval_abort_reason("bs1", since=time.time() + 60) is None
    # No abort stamped for another instance.
    assert tap._approval_abort_reason("bs2", since=0.0) is None


def test_require_approval_aborts_pending_click_on_restart(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """A restart-issued abort (stamped after the request started) makes the
    operator-decision wait give up: ok=False and the slot is cleared so the
    bot never taps the freshly-restarted game."""
    r = _RedisProxy(redis_sync)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    # Abort stamped in the (relative) future so it is always >= entered_at.
    r.set(
        "wos:ui:click_approval:abort:bs1",
        json.dumps({"at": time.time() + 60, "reason": "aborted_for_restart"}),
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is not None
    # Slot cleared by the non-approve cleanup path — next request can publish.
    assert r.get("wos:ui:click_approval:current:bs1") is None


def test_require_approval_aborts_while_waiting_for_page(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """When the approvals page is closed the request blocks in the page-open
    wait; a restart abort there returns (False, None) without ever publishing."""
    r = _RedisProxy(redis_sync)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    # No heartbeat → stuck waiting for the page to open.
    r.set(
        "wos:ui:click_approval:abort:bs1",
        json.dumps({"at": time.time() + 60, "reason": "aborted_for_restart"}),
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is None
    assert r.get("wos:ui:click_approval:current:bs1") is None


def test_require_approval_ignores_stale_abort(
    monkeypatch: Any, redis_sync: Any
) -> None:
    """An abort stamped before the request started belongs to an earlier
    (already finished) request and must not block the new one."""
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.set(
        "wos:ui:click_approval:abort:bs1",
        json.dumps({"at": time.time() - 60, "reason": "aborted_for_restart"}),
    )
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None
