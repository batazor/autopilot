"""Unit tests for ``config.tracing``.

OTel's TracerProvider can only be installed once per process — ``set_tracer_provider``
silently warns and bails on the second call. So all SDK-required assertions
share one ``setup_tracing`` invocation via the module-scoped fixture below;
the no-op-path assertions live in subprocess-isolated tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanContext, TraceFlags

_ENDPOINT = "http://localhost:4318"


@pytest.fixture(scope="module", autouse=True)
def _initialised_tracing(monkeypatch_module: pytest.MonkeyPatch) -> object:
    """Install the SDK once for this module so subsequent tests can use it.

    Using ``monkeypatch_module`` (the module-scoped variant we add below)
    keeps the env var scoped to this file — other test modules don't see it.

    The OTLP exporter is swapped for an in-memory exporter before init so
    teardown doesn't spam stderr with retries against an unreachable
    ``localhost:4318``. The endpoint env var still has to be present —
    ``setup_tracing`` uses it as the on/off switch.
    """
    monkeypatch_module.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _ENDPOINT)
    monkeypatch_module.delenv("OTEL_SDK_DISABLED", raising=False)
    # Patch both exporters the bootstrap imports so init wires in-memory
    # variants instead of OTLP — no network traffic, no retry storm during
    # SDK shutdown.
    import opentelemetry.exporter.otlp.proto.http.metric_exporter as _otlp_metric
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as _otlp_trace
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.metrics.export import (
        PeriodicExportingMetricReader as _RealPeriodicReader,
    )
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    monkeypatch_module.setattr(
        _otlp_trace, "OTLPSpanExporter", InMemorySpanExporter
    )
    # Replace PeriodicExportingMetricReader (which would otherwise call the
    # OTLP HTTP exporter every 60s) with the synchronous InMemoryMetricReader.
    # The bootstrap accesses the symbol via ``from … import …`` inside the
    # function, so we patch it on the source module.
    import opentelemetry.sdk.metrics.export as _metrics_export

    class _NoExportPeriodicReader(InMemoryMetricReader):
        # Drop-in stub so MeterProvider's constructor accepts it where it
        # expects a PeriodicExportingMetricReader. Inherits InMemoryMetricReader
        # behaviour (collect on shutdown, no network calls).
        def __init__(self, _exporter: object, **_kwargs: object) -> None:
            super().__init__()

    monkeypatch_module.setattr(
        _metrics_export, "PeriodicExportingMetricReader", _NoExportPeriodicReader
    )
    # The bootstrap also stubs the metric exporter import — keep it pointing
    # to the OTLPMetricExporter so the symbol resolves, but we never call it
    # because the reader stub above ignores its first arg.
    _ = _otlp_metric  # silence unused-import warning if linter pickles it
    _ = _RealPeriodicReader

    import config.tracing as tracing

    tracing.setup_tracing("scheduler", instance_id="bs9")
    return tracing


@pytest.fixture(scope="module")
def monkeypatch_module() -> object:
    # ``monkeypatch`` is function-scoped by default; this gives us module scope
    # so the env var stays set across all tests that share the SDK init.
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_setup_tracing_installs_sdk_provider(_initialised_tracing: object) -> None:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    res = provider.resource.attributes
    # All processes share ``service.name="wos"`` — role lives in
    # ``wos.component`` so Tempo's service list stays a single row.
    assert res["service.name"] == "wos"
    assert res["wos.component"] == "scheduler"
    assert res["service.namespace"] == "wos"
    assert res["service.instance.id"] == "bs9"


def test_setup_tracing_idempotent(_initialised_tracing: object) -> None:
    """Second call must not replace the provider — would double-export every span."""
    import config.tracing as tracing

    first = trace.get_tracer_provider()
    tracing.setup_tracing("worker", instance_id="bs2")
    assert trace.get_tracer_provider() is first


def test_inject_context_into_writes_traceparent(_initialised_tracing: object) -> None:
    """With an active recording span, search-friendly trace ids land in the carrier."""
    import config.tracing as tracing

    carrier: dict[str, Any] = {}
    with tracing.traced("inject_test") as span:
        expected = format(span.get_span_context().trace_id, "032x")
        tracing.inject_context_into(carrier)
    assert "traceparent" in carrier
    # W3C format: ``00-<32-hex trace_id>-<16-hex span_id>-<2-hex flags>``.
    assert carrier["traceparent"].count("-") == 3
    assert carrier["trace_id"] == expected


def test_inject_extract_round_trip(_initialised_tracing: object) -> None:
    """End-to-end: span → inject → extract → child span sees the parent's trace_id.

    Mirrors the runtime contract that makes ``scheduler.tick → task.run`` show up
    as one trace in Tempo: enqueue stamps the queue payload with traceparent,
    the worker extracts it, and the resulting ``task.run`` span hangs under
    the scheduler's parent span.
    """
    import config.tracing as tracing

    carrier: dict[str, Any] = {}
    with tracing.traced("producer") as parent_span:
        parent_trace_id = parent_span.get_span_context().trace_id
        tracing.inject_context_into(carrier)

    parent_ctx = tracing.context_from_carrier(carrier)
    assert parent_ctx is not None

    with tracing.get_tracer().start_as_current_span(
        "consumer", context=parent_ctx
    ) as child:
        assert child.get_span_context().trace_id == parent_trace_id


def test_traced_root_starts_new_trace_under_active_parent(
    _initialised_tracing: object,
) -> None:
    """Scenario spans use this helper so each run is searchable as its own trace."""
    import config.tracing as tracing

    with tracing.traced("worker.loop") as parent_span:
        parent_trace_id = parent_span.get_span_context().trace_id
        with tracing.traced_root("scenario.run mail.claim") as scenario_span:
            scenario_ctx = scenario_span.get_span_context()

    assert scenario_ctx.trace_id != parent_trace_id
    assert scenario_ctx.trace_id != 0


def test_context_from_carrier_returns_none_for_empty() -> None:
    """Pure unit-level — no SDK state required."""
    import config.tracing as tracing

    assert tracing.context_from_carrier(None) is None
    assert tracing.context_from_carrier({}) is None
    assert tracing.context_from_carrier({"unrelated": "x"}) is None


def test_context_from_carrier_with_traceparent_only() -> None:
    """A bare ``traceparent`` (no tracestate) is enough to attach a parent."""
    import config.tracing as tracing

    tp = "00-00000000000000000000000000000001-0000000000000001-01"
    ctx = tracing.context_from_carrier({"traceparent": tp})
    assert ctx is not None
    span = trace.get_current_span(ctx)
    sc: SpanContext = span.get_span_context()
    assert sc.trace_id == 1
    assert sc.span_id == 1
    assert sc.trace_flags == TraceFlags(0x01)


def test_traced_records_exception(_initialised_tracing: object) -> None:
    import config.tracing as tracing

    msg = "kaboom"
    with pytest.raises(ValueError, match=msg), tracing.traced("boom"):
        raise ValueError(msg)


def test_set_span_attributes_coerces_non_primitives(
    _initialised_tracing: object,
) -> None:
    """Dict / object attribute values get stringified instead of raising."""
    import config.tracing as tracing

    with tracing.traced("attrs_test") as span:
        tracing.set_span_attributes(
            span,
            ok_str="hi",
            ok_int=7,
            weird={"nested": True},
            none_dropped=None,
        )


def test_log_record_factory_injects_otel_ids(_initialised_tracing: object) -> None:
    """Custom factory in setup_tracing stamps every record with otelTraceID/SpanID.

    Outside any span: ``otelTraceID == "0"``. Inside a recording span: full
    32-hex trace id matching the active span's context. This is what the
    ``%(otelTraceID).8s`` placeholder in ``config/logging_stdout.py`` reads.
    """
    import logging

    import config.tracing as tracing

    rec = logging.makeLogRecord({})
    assert rec.otelTraceID == "0"  # ty: ignore[unresolved-attribute]
    assert rec.otelSpanID == "0"  # ty: ignore[unresolved-attribute]

    with tracing.traced("log_corr_test") as span:
        rec_in = logging.makeLogRecord({})
        expected = format(span.get_span_context().trace_id, "032x")
        assert rec_in.otelTraceID == expected  # ty: ignore[unresolved-attribute]
        assert rec_in.otelSpanID == format(  # ty: ignore[unresolved-attribute]
            span.get_span_context().span_id, "016x"
        )


def test_add_event_attaches_span_event(_initialised_tracing: object) -> None:
    """``add_event`` records a timestamped marker on the *current* span."""
    import config.tracing as tracing

    with tracing.traced("event_test") as span:
        tracing.add_event("milestone_a", count=3, label="hello")
        tracing.add_event("milestone_b")
        events = list(span.events)  # ty: ignore[unresolved-attribute]

    names = [e.name for e in events]
    assert "milestone_a" in names
    assert "milestone_b" in names
    a = next(e for e in events if e.name == "milestone_a")
    assert a.attributes.get("count") == 3
    assert a.attributes.get("label") == "hello"


def test_add_event_no_op_outside_span() -> None:
    """``add_event`` is safe when no span is active."""
    import config.tracing as tracing

    tracing.add_event("noop_event", x=1)  # Must not raise.


def test_metric_instruments_lazy_create(_initialised_tracing: object) -> None:
    """Each named instrument is created once and cached for subsequent calls."""
    import config.tracing as tracing

    h1 = tracing.task_duration_histogram()
    h2 = tracing.task_duration_histogram()
    assert h1 is h2

    s1 = tracing.screenshot_analysis_duration_histogram()
    s2 = tracing.screenshot_analysis_duration_histogram()
    assert s1 is s2

    c1 = tracing.dsl_exec_counter()
    c2 = tracing.dsl_exec_counter()
    assert c1 is c2


def test_metric_instruments_record_does_not_raise(_initialised_tracing: object) -> None:
    """Smoke: calling ``.record`` / ``.add`` with attributes is non-fatal."""
    import config.tracing as tracing

    tracing.task_duration_histogram().record(
        1.5, attributes={"task_type": "x", "scenario": "y", "outcome": "finished"}
    )
    tracing.screenshot_analysis_duration_histogram().record(
        0.4,
        attributes={
            "node": "main_city",
            "source": "rolling",
            "device_level_only": False,
            "task_busy": False,
            "outcome": "ok",
        },
    )
    tracing.dsl_match_score_histogram().record(
        0.87, attributes={"region": "main_city.menu", "scenario": "x", "matched": True}
    )
    tracing.dsl_exec_counter().add(
        1, attributes={"cmd": "fetch_player", "scenario": "x"}
    )
    tracing.queue_size_gauge().record(42)
    tracing.redis_command_counter().add(
        1, attributes={"command": "GET", "component": "test", "outcome": "ok"}
    )
    tracing.redis_command_duration_histogram().record(
        0.001, attributes={"command": "GET", "component": "test", "outcome": "ok"}
    )
    tracing.overlay_tab_red_dot_idle_counter().add(
        1,
        attributes={
            "instance_id": "bs1",
            "screen": "deals",
            "rule": "deals.tabs.visible_red_dot",
            "region": "deals.tabs_strip",
            "active_index": "0",
            "red_dot_indices": "1,2",
            "action": "push_red_dot_pages",
        },
    )


# ---------------------------------------------------------------------------
# No-op path — runs in a fresh subprocess so it sees a virgin OTel global.
# ---------------------------------------------------------------------------


def _run_subprocess_assert(snippet: str, env: dict[str, str]) -> None:
    """Execute ``snippet`` in a fresh interpreter; raise on non-zero exit."""
    full_env = dict(os.environ)
    full_env.update(env)
    full_env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)  # default for these tests
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"subprocess failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_setup_tracing_noop_without_endpoint() -> None:
    """No env var → setup_tracing is a no-op; ``_INITIALIZED`` stays False."""
    _run_subprocess_assert(
        """
        import config.tracing as t
        from opentelemetry.trace import NonRecordingSpan
        t.setup_tracing("test")
        assert t._INITIALIZED is False
        with t.traced("noop_test", foo="bar") as span:
            assert isinstance(span, NonRecordingSpan)
        carrier = {}
        t.inject_context_into(carrier)
        assert carrier == {}, carrier
        """,
        env={},
    )


def test_setup_tracing_disabled_via_flag() -> None:
    """``OTEL_SDK_DISABLED=true`` overrides a configured endpoint."""
    _run_subprocess_assert(
        """
        import config.tracing as t
        t.setup_tracing("test")
        assert t._INITIALIZED is False
        """,
        env={
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_SDK_DISABLED": "true",
        },
    )


def test_trace_id_hex_for_history_fallback_when_span_invalid() -> None:
    import config.tracing as tracing

    tid = tracing.trace_id_hex_for_history(
        span_ctx=trace.INVALID_SPAN_CONTEXT,
        fallback_seed="bs1:cron:foo:1.0",
    )
    assert len(tid) == 32
    assert tid == tracing.trace_id_hex_for_history(
        span_ctx=trace.INVALID_SPAN_CONTEXT,
        fallback_seed="bs1:cron:foo:1.0",
    )
