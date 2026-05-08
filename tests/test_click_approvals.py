from __future__ import annotations

import json
from typing import Any

import actions.tap as tap


class FakeRedis:
    def __init__(
        self,
        *,
        approve_on_current: bool = False,
        drop_current_after_publish: bool = False,
    ) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.approve_on_current = approve_on_current
        self.drop_current_after_publish = drop_current_after_publish

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        if self.approve_on_current and ":current:" in key:
            payload = json.loads(value)
            self.values[str(payload["response_key"])] = "approve"
        if self.drop_current_after_publish and ":current:" in key:
            self.values.pop(key, None)
        return True

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)

    def expire(self, key: str, ttl: int) -> bool:
        del ttl
        return key in self.values

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _patch_redis(monkeypatch: Any, fake: FakeRedis) -> None:
    monkeypatch.setattr(tap, "_redis", lambda: fake)
    monkeypatch.setattr(tap, "_APPROVAL_POLL_SECONDS", 0.0)
    monkeypatch.setattr(tap, "_APPROVAL_PUBLISH_WAIT_SECONDS", 0.01)


def test_require_approval_default_on_when_redis_key_missing(monkeypatch: Any) -> None:
    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None


def test_require_approval_explicit_off_bypasses(monkeypatch: Any) -> None:
    fake = FakeRedis()
    fake.values["wos:ui:click_approval:enabled:bs1"] = "0"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is None


def test_require_approval_uses_request_specific_response(monkeypatch: Any) -> None:
    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    assert req_id is not None
    assert fake.get(f"wos:ui:click_approval:response:{req_id}") == "approve"
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id
    assert current["status"] == "approved"


def test_require_approval_does_not_reuse_existing_pending_request(monkeypatch: Any) -> None:
    fake = FakeRedis()
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.values["wos:ui:click_approval:current:bs1"] = json.dumps(
        {
            "request_id": "adb:bs1:old",
            "response_key": "wos:ui:click_approval:response:adb:bs1:old",
            "status": "waiting",
        }
    )
    fake.values["wos:ui:click_approval:response:adb:bs1:old"] = "approve"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 10, "y": 20})

    assert ok is False
    assert req_id is None
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == "adb:bs1:old"


def test_require_approval_reaps_stale_pending_request_from_previous_scenario(
    monkeypatch: Any,
) -> None:
    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_task_region": "ads_rookie_value_pack",
        "current_scenario": "ads_rookie_value_pack",
    }
    fake.values["wos:ui:click_approval:current:bs1"] = json.dumps(
        {
            "request_id": "adb:bs1:old",
            "response_key": "wos:ui:click_approval:response:adb:bs1:old",
            "status": "waiting",
            "created_at": 1,
            "context": {"scenario": "who_i_am"},
        }
    )
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 10, "y": 20})

    assert ok is True
    assert req_id is not None
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["request_id"] == req_id
    assert current["context"]["scenario"] == "ads_rookie_value_pack"


def test_require_approval_set_node_drops_stale_task_region(monkeypatch: Any) -> None:
    """``set_node`` does not tap a region; payload context must NOT carry the
    task-level ``current_task_region`` from the previous step (would render a
    bogus region overlay on the approvals page)."""

    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_screen": "",
        "current_task_player": "765502864",
        "current_task_region": "ads_rookie_value_pack",
        "current_scenario": "ads_rookie_value_pack",
    }
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval(
        "bs1",
        {"type": "set_node", "set_node": "main_city"},
    )

    assert ok is True
    assert req_id is not None
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["type"] == "set_node"
    assert current["context"]["current_task_region"] == ""
    assert current["context"]["current_task_player"] == "765502864"
    assert current["context"]["scenario"] == "ads_rookie_value_pack"


def test_require_approval_tap_keeps_task_region(monkeypatch: Any) -> None:
    """A regular ``tap`` still receives the task-level ``current_task_region``
    so the approvals UI can highlight the region being clicked."""

    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_task_region": "ads_rookie_value_pack",
    }
    _patch_redis(monkeypatch, fake)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_region"] == "ads_rookie_value_pack"


def test_require_approval_tap_includes_threshold_and_score_context(monkeypatch: Any) -> None:
    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_task_region": "hand_pointer",
        "current_task_threshold": "0.92",
        "current_task_score": "0.9553",
        "current_scenario": "hand_pointer",
    }
    _patch_redis(monkeypatch, fake)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_threshold"] == "0.92"
    assert current["context"]["current_task_score"] == "0.9553"


def test_require_approval_tap_falls_back_to_last_overlay_hints(monkeypatch: Any) -> None:
    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_task_threshold": "",
        "current_task_score": "",
        "last_overlay_match_threshold": "0.92",
        "last_overlay_match_score": "0.951",
    }
    _patch_redis(monkeypatch, fake)

    ok, _req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is True
    current = json.loads(fake.get("wos:ui:click_approval:current:bs1") or "{}")
    assert current["context"]["current_task_threshold"] == "0.92"
    assert current["context"]["current_task_score"] == "0.951"


def test_require_approval_aborts_on_foreign_request(monkeypatch: Any) -> None:
    """If another request_id takes over the per-instance ``current`` slot
    while we are waiting (e.g. the previous request was cleared and a new
    one published), the older waiter treats that as a reject and exits.
    This is the only non-decision way out of the wait loop — there is no
    wall-clock timeout and no heartbeat-loss abort."""

    class ForeignInjector(FakeRedis):
        """Replaces the just-published ``current`` payload with a foreign
        ``request_id`` so the waiter detects that the slot was hijacked."""

        def set(
            self,
            key: str,
            value: str,
            *,
            ex: int | None = None,
            nx: bool = False,
        ) -> bool:
            ok = super().set(key, value, ex=ex, nx=nx)
            if ok and ":current:" in key and nx:
                # Force a foreign request_id without going through nx.
                self.values[key] = json.dumps(
                    {"request_id": "adb:bs1:other", "status": "waiting"}
                )
            return ok

    fake = ForeignInjector()
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is not None
    # Cleanup deleted only OUR request_id — the foreign payload remains.
    raw = fake.get("wos:ui:click_approval:current:bs1")
    assert raw is not None
    assert json.loads(raw)["request_id"] == "adb:bs1:other"
    assert fake.get(f"wos:ui:click_approval:response:{req_id}") is None
