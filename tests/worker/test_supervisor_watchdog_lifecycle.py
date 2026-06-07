from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

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
