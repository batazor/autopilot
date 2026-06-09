from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from licensing.models import LicenseError
from worker import async_supervisor, supervisor


@pytest.mark.asyncio
async def test_async_supervisor_starts_health_watchdog(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        async_supervisor,
        "bootstrap_runtime_observability",
        lambda _component: None,
    )
    monkeypatch.setattr(async_supervisor, "assert_startup_configs_valid", lambda: None)
    monkeypatch.setattr(
        async_supervisor,
        "ensure_health_watchdog_process",
        lambda: events.append("watchdog"),
    )

    async def _init_services() -> None:
        events.append("init")

    async def _close_services() -> None:
        events.append("close")

    async def _long_lived(*_args: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(async_supervisor, "init_app_services", _init_services)
    monkeypatch.setattr(async_supervisor, "aclose_app_services", _close_services)
    monkeypatch.setattr(
        async_supervisor,
        "shutdown_ortools_executor",
        lambda **_kwargs: events.append("shutdown"),
    )
    monkeypatch.setattr(
        async_supervisor,
        "get_settings",
        lambda: SimpleNamespace(instances=[]),
    )
    monkeypatch.setattr(async_supervisor, "_guarded_scheduler", _long_lived)
    monkeypatch.setattr(async_supervisor, "_reconcile_loop", _long_lived)

    stop = threading.Event()
    stop.set()

    await async_supervisor.run_forever_async(stop_event=stop)

    assert events[:2] == ["watchdog", "init"]
    assert "shutdown" in events
    assert "close" in events


def test_multiprocess_supervisor_starts_and_stops_health_watchdog(monkeypatch) -> None:
    events: list[str] = []

    class FakeSupervisor:
        def is_healthy(self) -> bool:
            return True

        def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(
        supervisor,
        "bootstrap_runtime_observability",
        lambda *_args, **_kwargs: events.append("bootstrap"),
    )
    monkeypatch.setattr(supervisor, "generate_fingerprint", lambda: "fingerprint")
    monkeypatch.setattr(supervisor, "load_settings", lambda: object())
    monkeypatch.setattr(supervisor, "set_settings", lambda _settings: None)
    monkeypatch.setattr(supervisor, "assert_startup_configs_valid", lambda: None)
    monkeypatch.setattr(supervisor, "_wait_for_license_gate", lambda: True)
    monkeypatch.setattr(
        supervisor,
        "ensure_health_watchdog_process",
        lambda: events.append("watchdog"),
    )
    monkeypatch.setattr(
        supervisor,
        "stop_health_watchdog_process",
        lambda: events.append("stop-watchdog"),
    )
    monkeypatch.setattr(
        supervisor.multiprocessing,
        "set_start_method",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(supervisor.telemetry, "bind_supervisor", lambda _supervisor: None)
    monkeypatch.setattr(
        supervisor,
        "shutdown_runtime_observability",
        lambda: events.append("shutdown"),
    )

    class _FakeHealthServer:
        def shutdown(self) -> None:
            events.append("stop-health-server")

    monkeypatch.setattr(
        supervisor,
        "start_health_server",
        lambda *_args, **_kwargs: (events.append("health-server"), _FakeHealthServer())[1],
    )
    monkeypatch.setattr(supervisor, "Supervisor", FakeSupervisor)

    supervisor.main()

    assert events == [
        "bootstrap",
        "watchdog",
        "health-server",
        "run",
        "stop-health-server",
        "stop-watchdog",
        "shutdown",
    ]


def test_license_gate_waits_until_license_appears(monkeypatch) -> None:
    events: list[str] = []

    class Claims:
        sub = "user@example.com"
        tier = "pro"

        def days_until_expiry(self) -> float:
            return 30.0

    attempts = iter(
        [
            LicenseError("no license found", code="missing"),
            Claims(),
        ]
    )

    def fake_load_license() -> Claims:
        result = next(attempts)
        if isinstance(result, LicenseError):
            raise result
        return result

    monkeypatch.setattr(supervisor, "generate_fingerprint", lambda: "ABCD-EFGH-IJKL-MNOP")
    monkeypatch.setattr(supervisor, "load_license", fake_load_license)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: events.append("sleep"))
    monkeypatch.setattr(supervisor.telemetry, "report_license_gate_failure", lambda code: events.append(f"fail:{code}"))
    monkeypatch.setattr(
        supervisor.telemetry,
        "bind_license_claims",
        lambda _claims, *, host_fingerprint: events.append(f"bind:{host_fingerprint}"),
    )

    assert supervisor._wait_for_license_gate() is True
    assert events == ["fail:missing", "sleep", "bind:ABCD-EFGH-IJKL-MNOP"]


def _settings_with_instances(*instances: Any) -> SimpleNamespace:
    return SimpleNamespace(instances=list(instances), redis=SimpleNamespace(url="redis://test"))


def _instance(
    instance_id: str,
    serial: str,
    *,
    screenshot_backend: str = "",
    input_backend: str = "",
    game: str = "wos",
) -> SimpleNamespace:
    return SimpleNamespace(
        instance_id=instance_id,
        bluestacks_window_title=serial,
        screenshot_backend=screenshot_backend,
        input_backend=input_backend,
        game=game,
        display=None,
    )


class _FakeWorkerProcess:
    def __init__(self, name: str) -> None:
        self.name = name
        self.pid = 100
        self.terminated = False
        self.killed = False

    def is_alive(self) -> bool:
        return not self.terminated and not self.killed

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def join(self, timeout: float | None = None) -> None:
        return None


class _FakePubSub:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.closed = False

    def get_message(self, timeout: float = 0) -> dict[str, object] | None:
        return self.messages.pop(0) if self.messages else None

    def close(self) -> None:
        self.closed = True


def test_multiprocess_supervisor_hot_adds_registered_device(monkeypatch) -> None:
    spawned: list[str] = []
    stamped: list[list[str]] = []
    initial = _settings_with_instances(_instance("bs1", "127.0.0.1:5555"))
    fresh = _settings_with_instances(
        _instance("bs1", "127.0.0.1:5555"),
        _instance("bs2", "127.0.0.1:5615"),
    )

    monkeypatch.setattr(supervisor, "get_settings", lambda: initial)
    sup = supervisor.Supervisor()
    sup._processes["bs1"] = _FakeWorkerProcess("bs1")  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    monkeypatch.setattr(supervisor, "set_settings", lambda _settings: None)
    monkeypatch.setattr(sup, "_read_fresh_settings", lambda: fresh)
    monkeypatch.setattr(
        sup,
        "_stamp_worker_started_at",
        lambda instances: stamped.append([i.instance_id for i in instances]),
    )
    monkeypatch.setattr(
        sup,
        "_spawn_worker",
        lambda inst: (spawned.append(inst.instance_id), _FakeWorkerProcess(inst.instance_id))[1],
    )

    sup._reconcile_devices()

    assert sorted(sup._processes) == ["bs1", "bs2"]
    assert spawned == ["bs2"]
    assert stamped == [["bs2"]]
    assert sup._settings is fresh


def test_multiprocess_supervisor_restarts_worker_when_device_config_changes(
    monkeypatch,
) -> None:
    spawned: list[str] = []
    initial_worker = _FakeWorkerProcess("bs1")
    initial = _settings_with_instances(_instance("bs1", "127.0.0.1:5555"))
    fresh = _settings_with_instances(
        _instance("bs1", "127.0.0.1:5615", screenshot_backend="adb"),
    )

    monkeypatch.setattr(supervisor, "get_settings", lambda: initial)
    sup = supervisor.Supervisor()
    sup._processes["bs1"] = initial_worker  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    monkeypatch.setattr(supervisor, "set_settings", lambda _settings: None)
    monkeypatch.setattr(sup, "_read_fresh_settings", lambda: fresh)
    monkeypatch.setattr(sup, "_stamp_worker_started_at", lambda _instances: None)
    monkeypatch.setattr(
        sup,
        "_spawn_worker",
        lambda inst: (spawned.append(inst.bluestacks_window_title), _FakeWorkerProcess(inst.instance_id))[1],
    )

    sup._reconcile_devices()

    assert initial_worker.terminated is True
    assert spawned == ["127.0.0.1:5615"]
    assert sorted(sup._processes) == ["bs1"]


def test_multiprocess_supervisor_reconcile_is_event_driven(monkeypatch) -> None:
    initial = _settings_with_instances(_instance("bs1", "127.0.0.1:5555"))
    monkeypatch.setattr(supervisor, "get_settings", lambda: initial)
    sup = supervisor.Supervisor()
    sup._device_reconcile_pubsub = _FakePubSub([])

    assert sup._device_reconcile_requested() is False

    sup._device_reconcile_pubsub = _FakePubSub(
        [{"type": "message", "data": "register:bs2"}]
    )

    assert sup._device_reconcile_requested() is True


def test_multiprocess_supervisor_subscribes_and_refreshes_before_spawn(
    monkeypatch,
) -> None:
    events: list[str] = []
    initial = _settings_with_instances()
    fresh = _settings_with_instances(_instance("bs2", "127.0.0.1:5615"))

    monkeypatch.setattr(supervisor, "get_settings", lambda: initial)
    sup = supervisor.Supervisor()

    def fake_refresh() -> None:
        events.append("refresh")
        sup._settings = fresh

    monkeypatch.setattr(
        sup,
        "_ensure_device_reconcile_subscription",
        lambda: events.append("subscribe") or True,
    )
    monkeypatch.setattr(sup, "_refresh_settings_snapshot", fake_refresh)
    monkeypatch.setattr(
        sup,
        "_stamp_worker_started_at",
        lambda instances: events.append(
            f"stamp:{','.join(i.instance_id for i in instances)}"
        ),
    )
    monkeypatch.setattr(
        sup,
        "_spawn_worker",
        lambda inst: (
            events.append(f"spawn:{inst.instance_id}"),
            _FakeWorkerProcess(inst.instance_id),
        )[1],
    )
    monkeypatch.setattr(
        sup,
        "_spawn_scheduler",
        lambda: (events.append("spawn:scheduler"), _FakeWorkerProcess("scheduler"))[1],
    )
    monkeypatch.setattr(sup, "_device_reconcile_requested", lambda: False)
    monkeypatch.setattr(
        supervisor.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    sup.run()

    assert events[:4] == ["subscribe", "refresh", "stamp:bs2", "spawn:bs2"]
    assert "spawn:scheduler" in events
