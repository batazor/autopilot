from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import worker.game_health_watchdog as watchdog
from adb.controller import ProcessDetection
from worker.game_health_watchdog import (
    _capture_restart_context,
    _is_game_running_after_retries,
    _record_restart_breadcrumb,
    restart_application_after_health_failure,
)

if TYPE_CHECKING:
    from adb import BotActions


class _FakeBotActions:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls = 0

    def is_game_running(self, _instance_id: str) -> bool:
        self.calls += 1
        if not self._results:
            return False
        return self._results.pop(0)


def test_process_retry_recovers_without_restart() -> None:
    # Transient pidof miss then alive → no restart (BlueStacks foreground parse
    # is no longer the criterion; process aliveness is).
    ba = _FakeBotActions([False, True])

    assert _is_game_running_after_retries(
        cast("BotActions", ba),
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 2


def test_process_retry_fails_after_all_attempts() -> None:
    # Process genuinely dead across all attempts → restart escalates.
    ba = _FakeBotActions([False, False, False, False])

    assert not _is_game_running_after_retries(
        cast("BotActions", ba),
        "bs1",
        stop=threading.Event(),
        retries=3,
        retry_interval=0,
    )
    assert ba.calls == 4


class _CtxBotActions:
    """Stub exposing the foreground + detection probes used at restart time."""

    def __init__(self, foreground: str, detection: ProcessDetection) -> None:
        self._foreground = foreground
        self._detection = detection

    def current_foreground_activity(self, _instance_id: str) -> str:
        return self._foreground

    def detect_game_process(self, _instance_id: str) -> ProcessDetection:
        return self._detection


class _FakeRedis:
    """Minimal sync-Redis surface for the breadcrumb writer."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def hincrby(self, key: str, field: str, amount: int) -> int:
        h = self.hashes.setdefault(key, {})
        h[field] = str(int(h.get(field, "0")) + amount)
        return int(h[field])

    def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, _key: str, _start: int, _stop: int) -> None:
        pass


class _FakeRecoveryRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def publish(self, key: str, value: str) -> None:
        self.published.append((key, value))

    def delete(self, key: str) -> None:
        self.deleted.append(key)


class _LaunchOnlyBotActions:
    latest: _LaunchOnlyBotActions | None = None

    def __init__(self, _settings: object) -> None:
        type(self).latest = self
        self.running = False
        self.launch_calls: list[tuple[str, bool]] = []
        self.restart_calls: list[str] = []

    def ensure_game_foreground(
        self,
        instance_id: str,
        *,
        require_approval: bool = True,
    ) -> bool:
        self.launch_calls.append((instance_id, require_approval))
        self.running = True
        return True

    def is_game_running(self, _instance_id: str) -> bool:
        return self.running

    def restart_application(self, instance_id: str) -> bool:
        self.restart_calls.append(instance_id)
        return True


def test_capture_restart_context_clean_miss_means_process_gone() -> None:
    ba = _CtxBotActions(
        "com.bluestacks.appmarket/.Main",
        ProcessDetection(found=False, pids=[], method_used="ps", error=None),
    )
    foreground, detection = _capture_restart_context(cast("BotActions", ba), "bs1")
    assert foreground == "com.bluestacks.appmarket/.Main"
    assert "clean_miss" in detection
    assert "method=ps" in detection


def test_capture_restart_context_error_means_adb_flake() -> None:
    ba = _CtxBotActions(
        "",
        ProcessDetection(found=False, pids=[], method_used="none", error="timed out"),
    )
    _foreground, detection = _capture_restart_context(cast("BotActions", ba), "bs1")
    assert "error=timed out" in detection


def test_record_restart_breadcrumb_persists_reason_and_count() -> None:
    r = _FakeRedis()
    key = "wos:instance:bs1:state"

    _record_restart_breadcrumb(
        cast("object", r),  # type: ignore[arg-type]
        "bs1",
        foreground="com.android.launcher/.Home",
        detection="method=ps found=False clean_miss",
    )
    _record_restart_breadcrumb(
        cast("object", r),  # type: ignore[arg-type]
        "bs1",
        foreground="com.gof.global/.MainActivity",
        detection="method=none found=False error=timed out",
    )

    h = r.hashes[key]
    assert h["game_restart_count"] == "2"
    assert h["last_game_restart_foreground"] == "com.gof.global/.MainActivity"
    assert "last_game_restart_at" in h

    rows = [json.loads(x) for x in r.lists["wos:debug:timeline:bs1"]]
    assert rows[0]["event"] == "game.restart"
    assert rows[0]["foreground"] == "com.gof.global/.MainActivity"
    assert rows[0]["reason"] == "process_dead_after_retries"


def test_health_recovery_starts_missing_application_without_restart(monkeypatch) -> None:
    redis = _FakeRecoveryRedis()
    monkeypatch.setattr(watchdog, "BotActions", _LaunchOnlyBotActions)
    monkeypatch.setattr(watchdog.time, "sleep", lambda _seconds: None)

    restart_application_after_health_failure(
        "bs1",
        cast("object", redis),  # type: ignore[arg-type]
        cast("object", SimpleNamespace()),  # type: ignore[arg-type]
    )

    actions = _LaunchOnlyBotActions.latest
    assert actions is not None
    assert actions.launch_calls == [("bs1", False), ("bs1", False)]
    assert actions.restart_calls == []
    assert redis.deleted == ["wos:instance:bs1:lock"]
    state = redis.hashes["wos:instance:bs1:state"]
    assert state["state"] == "ready"
    assert state["paused"] == "0"
    assert state["auto_paused"] == "0"


def test_reload_settings_detects_added_and_removed_devices(monkeypatch) -> None:
    from types import SimpleNamespace

    import worker.game_health_watchdog as w

    state = {"ids": ["bs1"]}
    monkeypatch.setattr(w, "invalidate_device_registry", lambda: None, raising=False)
    monkeypatch.setattr(
        w,
        "load_settings",
        lambda: SimpleNamespace(
            instances=[SimpleNamespace(instance_id=i) for i in state["ids"]]
        ),
    )
    rebinds: list[object] = []
    monkeypatch.setattr(w, "set_settings", lambda s: rebinds.append(s))

    # First call vs the startup id set {bs1} → unchanged, no rebind.
    _s, ids, changed = w._reload_settings_if_devices_changed({"bs1"})
    assert ids == {"bs1"} and changed is False and rebinds == []

    # bs2 registered after startup → change detected + settings rebound.
    state["ids"] = ["bs1", "bs2"]
    _s, ids, changed = w._reload_settings_if_devices_changed({"bs1"})
    assert ids == {"bs1", "bs2"} and changed is True and len(rebinds) == 1

    # bs1 unregistered → change detected.
    state["ids"] = ["bs2"]
    _s, ids, changed = w._reload_settings_if_devices_changed({"bs1", "bs2"})
    assert ids == {"bs2"} and changed is True
