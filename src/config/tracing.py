"""OpenTelemetry bootstrap and helpers.

Idempotent, env-driven SDK setup so every process (supervisor, scheduler,
per-instance worker, CLI) calls :func:`setup_tracing` once at boot and gets
a tracer that exports to whichever OTLP endpoint the operator configured.

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset (or ``OTEL_SDK_DISABLED=true``),
the call is a no-op: :func:`traced` yields the OTel API's no-op span (zero
allocations beyond the context-manager frame) and :func:`inject_context_into`
writes nothing — the bot runs exactly as before.

Env-vars (read directly by the OTel SDK; we don't re-export):

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — collector URL (e.g.
  ``https://otlp-gateway-prod-us-central-0.grafana.net/otlp``).
* ``OTEL_EXPORTER_OTLP_PROTOCOL`` — ``http/protobuf`` (this module ships only
  the HTTP exporter; gRPC needs a different package).
* ``OTEL_EXPORTER_OTLP_HEADERS`` — auth, e.g.
  ``Authorization=Basic <base64(instance_id:api_key)>``.
* ``OTEL_TRACES_SAMPLER`` / ``OTEL_TRACES_SAMPLER_ARG`` — defaults to
  ParentBased(AlwaysOn).

All processes share ``service.name="wos"``. The role (supervisor / scheduler
/ worker / cli / ui) lives in the ``wos.component`` resource attribute so a
TraceQL filter like ``{wos.component="worker"}`` still selects per-role
spans without splitting Tempo's service list.
"""
from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata as _md
from typing import Any

from opentelemetry import context as _otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

_TRACER_NAME = "wos"
_INITIALIZED = False
_PROCESS_GUARD_ENV = "WOS_OTEL_TRACING_INITIALIZED_PID"


def _is_truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _project_version() -> str:
    try:
        return _md.version("whiteout-survival-autopilot")
    except _md.PackageNotFoundError:
        return "0.0.0"


_LOG_FACTORY_INSTALLED = False


def _install_log_record_factory() -> None:
    """Patch :func:`logging.setLogRecordFactory` to inject OTel ids on every record.

    Idempotent: a module-global flag short-circuits a second install so we
    don't end up with N wrappers stacked after re-imports / forks.
    """
    global _LOG_FACTORY_INSTALLED
    if _LOG_FACTORY_INSTALLED:
        return
    import logging as _logging

    base_factory = _logging.getLogRecordFactory()
    invalid_span = trace.INVALID_SPAN_CONTEXT

    def _factory(*args: Any, **kwargs: Any) -> _logging.LogRecord:
        record = base_factory(*args, **kwargs)
        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else invalid_span
        if ctx.trace_id:
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")
            record.otelTraceSampled = bool(ctx.trace_flags.sampled)
        else:
            record.otelTraceID = "0"
            record.otelSpanID = "0"
            record.otelTraceSampled = False
        return record

    _logging.setLogRecordFactory(_factory)
    _LOG_FACTORY_INSTALLED = True


def setup_tracing(component: str, *, instance_id: str | None = None) -> None:
    """Initialize the OTel SDK for the calling process.

    All processes share ``service.name="wos"`` so Tempo's service list stays
    a single row instead of fanning into ``wos.supervisor`` /
    ``wos.scheduler`` / ``wos.worker`` / etc. The role is still queryable
    via the ``wos.component`` resource attribute (e.g. ``{component=worker}``).

    Args:
        component: short role label — ``supervisor``, ``scheduler``,
            ``worker``, ``cli``, ``ui``. Stamped onto every span as
            ``wos.component``.
        instance_id: ``service.instance.id``. Defaults to ``hostname`` for
            non-worker processes; workers pass their BlueStacks id so each
            instance is distinguishable in Tempo.

    Safe to call multiple times — second call is a no-op. After ``spawn``-ed
    multiprocessing children re-import this module, so each child must call
    this from its own entry point (the parent's setup does not propagate).
    """
    global _INITIALIZED
    current_pid = str(os.getpid())
    if _INITIALIZED or os.environ.get(_PROCESS_GUARD_ENV) == current_pid:
        _INITIALIZED = True
        return

    # Two off-switches — either silences the SDK without touching code paths.
    if _is_truthy_env("OTEL_SDK_DISABLED"):
        return
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip():
        # No collector configured — keep the no-op tracer the API ships with.
        return

    # Imports kept inside the function so projects without the SDK installed
    # (or with it explicitly disabled) don't pay the import cost.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": "wos",
            "service.namespace": os.environ.get("OTEL_SERVICE_NAMESPACE") or "wos",
            "service.instance.id": instance_id or socket.gethostname(),
            "service.version": _project_version(),
            "wos.component": component,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Metrics share the OTLP endpoint / headers / protocol envvars with traces;
    # the OTLPMetricExporter reads them automatically. Periodic reader pushes
    # every 60 s by default — fast enough for most dashboards and
    # cheap relative to per-tick span volume.
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )
    metrics.set_meter_provider(meter_provider)

    # Auto-instrument redis-py — covers every hget/hset/zadd/eval round-trip
    # made by both the sync and async clients with no further changes.
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception:
        # Instrumentation failure must not block the process boot — the
        # manual spans on the hot path still work without it.
        logger.warning("RedisInstrumentor.instrument() failed", exc_info=True)

    # Stamp every ``logging.LogRecord`` with ``otelTraceID`` / ``otelSpanID``
    # via a custom factory wrapper. ``LoggingInstrumentor`` would do this too,
    # but only when ``set_logging_format=True`` — and that calls
    # ``logging.basicConfig`` with the OTel default format, clobbering our
    # custom format from ``config/logging_stdout.py``. The wrapper below sets
    # the attrs unconditionally so the existing format's
    # ``%(otelTraceID).8s`` placeholder always renders (``"0"`` outside any
    # span; the 32-hex trace id inside one).
    _install_log_record_factory()

    _INITIALIZED = True
    os.environ[_PROCESS_GUARD_ENV] = current_pid
    logger.info(
        "OpenTelemetry tracing enabled — component=%s instance=%s endpoint=%s",
        component,
        instance_id or socket.gethostname(),
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def traced(name: str, **attrs: Any) -> Iterator[Span]:
    """Start a span; record exceptions, then end. Zero-cost when SDK is off.

    Exceptions are re-raised after being recorded so callers see them as
    usual — the tracer is observation-only.
    """
    tracer = get_tracer()
    # ``start_as_current_span`` is the cheapest path: when the global
    # provider is the no-op default, both span creation and ``set_attribute``
    # short-circuit inside the API.
    with tracer.start_as_current_span(name) as span:
        if attrs:
            for k, v in attrs.items():
                if v is None:
                    continue
                # OTel attribute values must be primitive or sequences of
                # primitives. Coerce anything else to str so callers can pass
                # rich values without manual conversion.
                if isinstance(v, (str, bool, int, float)):
                    span.set_attribute(k, v)
                elif isinstance(v, (list, tuple)) and all(
                    isinstance(x, (str, bool, int, float)) for x in v
                ):
                    span.set_attribute(k, list(v))
                else:
                    span.set_attribute(k, str(v))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def set_span_attributes(span: Span, **attrs: Any) -> None:
    """Set multiple attributes on ``span`` with the same coercion as :func:`traced`."""
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            span.set_attribute(k, v)
        elif isinstance(v, (list, tuple)) and all(
            isinstance(x, (str, bool, int, float)) for x in v
        ):
            span.set_attribute(k, list(v))
        else:
            span.set_attribute(k, str(v))


def add_event(name: str, **attrs: Any) -> None:
    """Attach a timestamped event to the *current* span.

    Cheaper than nesting another span when you just want a "milestone reached"
    marker inside a longer operation (screencap done, OCR call returned, …).
    No-op when there is no active recording span — safe to sprinkle on hot
    paths that may run with the SDK disabled.
    """
    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    coerced: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            coerced[k] = v
        elif isinstance(v, (list, tuple)) and all(
            isinstance(x, (str, bool, int, float)) for x in v
        ):
            coerced[k] = list(v)
        else:
            coerced[k] = str(v)
    span.add_event(name, attributes=coerced)


def inject_context_into(carrier: dict[str, Any]) -> None:
    """Write ``traceparent`` (and ``tracestate`` when present) into ``carrier``.

    Used at enqueue time so the downstream worker can continue the same trace.
    No-op when there is no active span.
    """
    propagate.inject(carrier)


# ---------------------------------------------------------------------------
# Metrics — named instruments shared across the codebase.
# ---------------------------------------------------------------------------
#
# Lazy-init via module-level cache so tests / scripts that never call any
# metric helper don't pay for instrument creation. The OTel API itself is a
# no-op when no MeterProvider is installed, so accessing instruments before
# ``setup_tracing`` is safe (calls just discard the value).

_METER_NAME = "wos"
_METRICS_CACHE: dict[str, Any] = {}


def get_meter() -> metrics.Meter:
    return metrics.get_meter(_METER_NAME)


def task_duration_histogram() -> Histogram:
    """End-to-end task wall-clock duration, partitioned by ``task_type`` / ``scenario``."""
    h = _METRICS_CACHE.get("task_duration")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.task.duration",
            unit="s",
            description="Wall-clock time from task pop to terminal event.",
        )
        _METRICS_CACHE["task_duration"] = h
    return h


def dsl_match_score_histogram() -> Histogram:
    """Distribution of ``score`` for every ``dsl.match`` step.

    Tagged by ``region`` / ``scenario`` so panels can spot regions whose
    matches consistently sit near the threshold (a sign the threshold is
    poorly calibrated, or the template needs a refresh).
    """
    h = _METRICS_CACHE.get("dsl_match_score")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.dsl.match.score",
            description="Combined match score (NCC × color × edge).",
        )
        _METRICS_CACHE["dsl_match_score"] = h
    return h


def dsl_exec_counter() -> Counter:
    """Number of ``exec:`` steps invoked, partitioned by ``cmd``."""
    c = _METRICS_CACHE.get("dsl_exec_count")
    if c is None:
        c = get_meter().create_counter(
            name="wos.dsl.exec.count",
            description="DSL exec handler invocations by name.",
        )
        _METRICS_CACHE["dsl_exec_count"] = c
    return c


def queue_size_gauge() -> Histogram:
    """Per-tick queue length emitted by the scheduler.

    OTel Python's UpDownCounter is the closest stable analog to a gauge for
    monotonic snapshots; a Histogram lets us observe distribution and pick
    p50/p95 in Grafana, which is more useful than the latest value alone.
    """
    h = _METRICS_CACHE.get("queue_size")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.queue.size",
            description="Total queued items across all instances per scheduler tick.",
        )
        _METRICS_CACHE["queue_size"] = h
    return h


def context_from_carrier(carrier: dict[str, Any] | None) -> _otel_context.Context | None:
    """Extract a parent context from a queue payload's ``traceparent`` field.

    Returns ``None`` if ``carrier`` lacks W3C trace headers; callers pass the
    result straight to ``start_as_current_span(context=...)`` (which accepts
    ``None`` to mean "use the current context").
    """
    if not carrier:
        return None
    if "traceparent" not in carrier:
        return None
    return propagate.extract(carrier)
