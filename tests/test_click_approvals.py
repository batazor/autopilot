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

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _patch_redis(monkeypatch: Any, fake: FakeRedis) -> None:
    monkeypatch.setattr(tap, "_redis", lambda: fake)
    monkeypatch.setattr(tap, "_APPROVAL_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(tap, "_APPROVAL_POLL_SECONDS", 0.0)


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


def test_require_approval_set_node_drops_stale_task_region(monkeypatch: Any) -> None:
    """``set_node`` does not tap a region; payload context must NOT carry the
    task-level ``current_task_region`` from the previous step (would render a
    bogus region overlay on the approvals page)."""

    fake = FakeRedis(approve_on_current=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    fake.hashes["wos:instance:bs1:state"] = {
        "current_screen": "",
        "current_task_player": "111111111",
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
    assert current["context"]["current_task_player"] == "111111111"
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


def test_require_approval_times_out_when_current_cleared_without_response(monkeypatch: Any) -> None:
    fake = FakeRedis(drop_current_after_publish=True)
    fake.values["wos:ui:click_approval:enabled:bs1"] = "1"
    fake.values["wos:ui:click_approval:heartbeat:bs1"] = "1"
    _patch_redis(monkeypatch, fake)

    ok, req_id = tap._require_approval("bs1", {"type": "tap", "x": 1, "y": 2})

    assert ok is False
    assert req_id is not None
    assert fake.get("wos:ui:click_approval:current:bs1") is None
    assert fake.get(f"wos:ui:click_approval:response:{req_id}") is None
