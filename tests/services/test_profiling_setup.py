from __future__ import annotations

import importlib
import sys
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_PYROSCOPE_ENV = [
    "PYROSCOPE_SERVER_ADDRESS",
    "PYROSCOPE_BASIC_AUTH_USERNAME",
    "PYROSCOPE_BASIC_AUTH_PASSWORD",
    "PYROSCOPE_TENANT_ID",
    "PYROSCOPE_SAMPLE_RATE",
    "PYROSCOPE_APPLICATION_NAME",
    "PYROSCOPE_DISABLED",
    "WOS_PYROSCOPE_INITIALIZED_PID",
]


def _fresh_profiling(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    for name in _PYROSCOPE_ENV:
        monkeypatch.delenv(name, raising=False)
    import config.profiling as profiling

    return importlib.reload(profiling)


def test_setup_profiling_configures_pyroscope_once(monkeypatch: pytest.MonkeyPatch) -> None:
    profiling = _fresh_profiling(monkeypatch)
    calls: list[dict[str, object]] = []
    fake_pyroscope = types.SimpleNamespace(configure=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "pyroscope", fake_pyroscope)
    monkeypatch.setattr(profiling, "_attach_span_processor", lambda: False)
    monkeypatch.setattr(profiling, "_project_version", lambda: "test-version")
    monkeypatch.setenv("PYROSCOPE_SERVER_ADDRESS", "http://profiles.local")
    monkeypatch.setenv("PYROSCOPE_BASIC_AUTH_USERNAME", "instance")
    monkeypatch.setenv("PYROSCOPE_BASIC_AUTH_PASSWORD", "token")
    monkeypatch.setenv("PYROSCOPE_TENANT_ID", "tenant-a")
    monkeypatch.setenv("PYROSCOPE_SAMPLE_RATE", "50")

    profiling.setup_profiling("worker", instance_id="bs1")
    profiling.setup_profiling("worker", instance_id="bs1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["application_name"] == "wos"
    assert kwargs["server_address"] == "http://profiles.local"
    assert kwargs["sample_rate"] == 50
    assert kwargs["basic_auth_username"] == "instance"
    assert kwargs["basic_auth_password"] == "token"
    assert kwargs["tenant_id"] == "tenant-a"
    assert kwargs["tags"] == {
        "wos_component": "worker",
        "service_namespace": "wos",
        "service_instance_id": "bs1",
        "service_version": "test-version",
    }


def test_setup_profiling_reinitializes_after_inherited_parent_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    profiling = _fresh_profiling(monkeypatch)
    calls: list[dict[str, object]] = []
    fake_pyroscope = types.SimpleNamespace(configure=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "pyroscope", fake_pyroscope)
    monkeypatch.setattr(profiling, "_attach_span_processor", lambda: False)
    monkeypatch.setattr(profiling.os, "getpid", lambda: 200)
    monkeypatch.setenv("PYROSCOPE_SERVER_ADDRESS", "http://profiles.local")
    monkeypatch.setenv("WOS_PYROSCOPE_INITIALIZED_PID", "100")
    profiling._INITIALIZED = True
    profiling._INITIALIZED_PID = "100"

    profiling.setup_profiling("scheduler")

    assert len(calls) == 1
    assert profiling._INITIALIZED_PID == "200"
    assert profiling.os.environ["WOS_PYROSCOPE_INITIALIZED_PID"] == "200"


def test_setup_profiling_ignores_invalid_sample_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    profiling = _fresh_profiling(monkeypatch)
    calls: list[dict[str, object]] = []
    fake_pyroscope = types.SimpleNamespace(configure=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setitem(sys.modules, "pyroscope", fake_pyroscope)
    monkeypatch.setattr(profiling, "_attach_span_processor", lambda: False)
    monkeypatch.setenv("PYROSCOPE_SERVER_ADDRESS", "http://profiles.local")
    monkeypatch.setenv("PYROSCOPE_SAMPLE_RATE", "0")

    profiling.setup_profiling("worker", instance_id="bs1")

    assert len(calls) == 1
    assert "sample_rate" not in calls[0]


def test_setup_profiling_never_raises_on_configure_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    profiling = _fresh_profiling(monkeypatch)

    def _raise_configure(**_kwargs: object) -> None:
        msg = "profile backend unavailable"
        raise RuntimeError(msg)

    fake_pyroscope = types.SimpleNamespace(configure=_raise_configure)
    monkeypatch.setitem(sys.modules, "pyroscope", fake_pyroscope)
    monkeypatch.setenv("PYROSCOPE_SERVER_ADDRESS", "http://profiles.local")

    profiling.setup_profiling("worker", instance_id="bs1")

    assert profiling._INITIALIZED is False
    assert "WOS_PYROSCOPE_INITIALIZED_PID" not in profiling.os.environ


def test_attach_span_processor_failure_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    profiling = _fresh_profiling(monkeypatch)

    class _Processor:
        pass

    class _Provider:
        def add_span_processor(self, _processor: object) -> None:
            msg = "provider rejected processor"
            raise RuntimeError(msg)

    fake_pyroscope = types.ModuleType("pyroscope")
    fake_otel = types.ModuleType("pyroscope.otel")
    fake_otel.PyroscopeSpanProcessor = _Processor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyroscope", fake_pyroscope)
    monkeypatch.setitem(sys.modules, "pyroscope.otel", fake_otel)
    monkeypatch.setattr(profiling.trace, "get_tracer_provider", lambda: _Provider())

    assert profiling._attach_span_processor() is False
