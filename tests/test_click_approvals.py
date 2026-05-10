from __future__ import annotations

import json
from typing import Any

import pytest

import actions.tap as tap

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
        drop_current_after_publish: bool = False,
        inject_foreign_current: bool = False,
    ) -> None:
        self._r = client
        self._approve_on_current = approve_on_current
        self._drop_current_after_publish = drop_current_after_publish
        self._inject_foreign_current = inject_foreign_current

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        ok = bool(self._r.set(key, value, ex=ex, nx=nx))
        if not ok:
            return False

        if ":current:" in key:
            if self._inject_foreign_current:
                # Hijack the current slot with a foreign request_id.
                cur = json.loads(value)
                cur["request_id"] = "adb:bs1:foreign"
                cur["response_key"] = "wos:ui:click_approval:response:adb:bs1:foreign"
                self._r.set(key, json.dumps(cur), ex=ex)
                self._inject_foreign_current = False
                return True

            if self._approve_on_current:
                payload = json.loads(value)
                self._r.set(str(payload["response_key"]), "approve")
            if self._drop_current_after_publish:
                self._r.delete(key)
        return True

    def delete(self, key: str) -> int:
        return int(self._r.delete(key))

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._r.expire(key, ttl))

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
    assert current["context"]["current_task_region"] == ""
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
    assert current["context"]["current_task_region"] == ""
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
    r = _RedisProxy(redis_sync, approve_on_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    r.hset(
        "wos:instance:bs1:state",
        mapping={
            "current_task_threshold": "",
            "current_task_score": "",
            "last_overlay_match_threshold": "0.92",
            "last_overlay_match_score": "0.951",
        },
    )
    _patch_redis(monkeypatch, r)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(r.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_threshold"] == "0.92"
    assert current["context"]["current_task_score"] == "0.951"


def test_require_approval_aborts_on_foreign_request(monkeypatch: Any, redis_sync: Any) -> None:
    r = _RedisProxy(redis_sync, inject_foreign_current=True)
    r.set("wos:ui:click_approval:enabled:bs1", "1")
    r.set("wos:ui:click_approval:heartbeat:bs1", "1")
    _patch_redis(monkeypatch, r)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is not None
