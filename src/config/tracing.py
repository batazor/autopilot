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
from contextlib import contextmanager
from importlib import metadata as _md
from typing import TYPE_CHECKING, Any

from opentelemetry import context as _otel_context
from opentelemetry import metrics, propagate, trace

if TYPE_CHECKING:
    from collections.abc import Iterator

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


# ---------------------------------------------------------------------------
# Rate-limit-aware OTLP metric exporter
# ---------------------------------------------------------------------------
#
# The stock HTTP metric exporter treats 429 as non-retryable
# (``opentelemetry.exporter.otlp.proto.http._common._is_retryable`` covers
# 408 and 5xx only). On Grafana Cloud's free OTLP gateway — which throttles
# us when N processes (supervisor + scheduler + N workers + api) push every
# 5 min — that means each batch logs ERROR, returns FAILURE, and the next
# tick 5 min later hits the same wall. No back-off; a steady drum-beat of
# 429s at the gateway and ERROR lines in the journal.
#
# This wrapper:
#   * Honors ``Retry-After`` on 429 (falls back to a 10 min default when the
#     header is absent) and short-circuits ``export`` to SUCCESS while the
#     cool-down is active — so we actually stop pounding the gateway.
#   * Logs a single WARN per cool-down window (not per batch).
#   * Pairs with a logging filter on the SDK exporter's logger that swallows
#     the base class's "Failed to export metrics batch ... 429" ERROR.

_429_LOGGER_NAME = "opentelemetry.exporter.otlp.proto.http.metric_exporter"
_429_FILTER_INSTALLED = False


class _Suppress429ExportError(logging.Filter):
    """Drop the SDK's per-batch ERROR for 429; our wrapper logs once per window."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        msg = record.getMessage()
        return not ("Failed to export metrics batch" in msg and "429" in msg)


def _install_429_log_filter() -> None:
    global _429_FILTER_INSTALLED
    if _429_FILTER_INSTALLED:
        return
    logging.getLogger(_429_LOGGER_NAME).addFilter(_Suppress429ExportError())
    _429_FILTER_INSTALLED = True


def _build_rate_limited_metric_exporter_class() -> type:
    """Build the wrapper lazily so import cost stays inside ``setup_tracing``."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics.export import MetricExportResult

    class _RateLimitedExporter(OTLPMetricExporter):
        _MIN_COOLDOWN_SEC = 60.0
        _DEFAULT_COOLDOWN_SEC = 600.0  # 10 min when no Retry-After header

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._cooldown_until_monotonic = 0.0
            self._last_warn_monotonic = -float("inf")

        def _export(self, serialized_data: Any, timeout_sec: float) -> Any:
            import time as _time

            resp = super()._export(serialized_data, timeout_sec)
            if getattr(resp, "status_code", None) == 429:
                cooldown = self._DEFAULT_COOLDOWN_SEC
                retry_after = None
                try:
                    retry_after = resp.headers.get("Retry-After")
                except AttributeError:
                    retry_after = None
                if retry_after:
                    try:
                        cooldown = max(
                            self._MIN_COOLDOWN_SEC, float(retry_after)
                        )
                    except ValueError:
                        cooldown = self._DEFAULT_COOLDOWN_SEC
                now = _time.monotonic()
                self._cooldown_until_monotonic = now + cooldown
                if now - self._last_warn_monotonic >= cooldown:
                    logger.warning(
                        "OTLP metrics rate-limited (429) by %s — backing off for %.0fs",
                        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "<unset>",
                        cooldown,
                    )
                    self._last_warn_monotonic = now
            return resp

        def export(
            self,
            metrics_data: Any,
            timeout_millis: float = 10000,
            **kwargs: Any,
        ) -> MetricExportResult:
            import time as _time

            if _time.monotonic() < self._cooldown_until_monotonic:
                # Drop the batch silently while the gateway is cooling down.
                # Returning SUCCESS keeps the PeriodicExportingMetricReader
                # quiet — we already warned once at cool-down entry.
                return MetricExportResult.SUCCESS
            return super().export(metrics_data, timeout_millis, **kwargs)

    return _RateLimitedExporter


def _RateLimitedOTLPMetricExporter(*args: Any, **kwargs: Any) -> Any:
    """Factory that builds (once) and instantiates the wrapper class."""
    cls = globals().get("__RATE_LIMITED_EXPORTER_CLS")
    if cls is None:
        cls = _build_rate_limited_metric_exporter_class()
        globals()["__RATE_LIMITED_EXPORTER_CLS"] = cls
    _install_429_log_filter()
    return cls(*args, **kwargs)


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
    # ``OTEL_TRACES_EXPORTER=none`` opts out of span export at the source.
    # The env var only affects SDK auto-detection — explicitly constructing
    # ``OTLPSpanExporter()`` here would still push, so we gate it ourselves
    # (mirrors the ``OTEL_METRICS_EXPORTER=none`` gate below). The maintainer's
    # Grafana Cloud access policy carries ``metrics:write`` only, so every span
    # batch would otherwise log ERROR with a 401 from the OTLP gateway.
    if (os.environ.get("OTEL_TRACES_EXPORTER") or "").strip().lower() != "none":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Metrics share the OTLP endpoint / headers / protocol envvars with traces;
    # the OTLPMetricExporter reads them automatically.
    # ``OTEL_METRICS_EXPORTER=none`` opts out (mirrors the matching gate in
    # ``logging_otel`` for ``OTEL_LOGS_EXPORTER=none``) — useful when the
    # configured collector only accepts traces (e.g. a dedicated Tempo OTLP
    # endpoint), so the metric exporter doesn't pound it with 404s.
    # Default export interval is 5 min (vs SDK's 60 s): we run N worker
    # processes + supervisor + scheduler + api, each pushing independently —
    # Grafana Cloud's free tier returns 429 if every process hits it every
    # minute. The metrics we publish (heartbeat / uptime / workers) are
    # gauges, so 5 min cadence is plenty for dashboards.
    # ``OTEL_METRIC_EXPORT_INTERVAL`` (milliseconds) overrides.
    if (os.environ.get("OTEL_METRICS_EXPORTER") or "").strip().lower() != "none":
        try:
            export_interval_millis = int(
                (os.environ.get("OTEL_METRIC_EXPORT_INTERVAL") or "").strip()
                or 300_000
            )
        except ValueError:
            export_interval_millis = 300_000
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    _RateLimitedOTLPMetricExporter(),
                    export_interval_millis=export_interval_millis,
                )
            ],
        )
        metrics.set_meter_provider(meter_provider)

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


def shutdown_tracing() -> None:
    """Flush SDK exporters before interpreter atexit. Safe if never initialised."""
    global _INITIALIZED
    if not _INITIALIZED:
        return
    try:
        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
        meter_provider = metrics.get_meter_provider()
        meter_shutdown = getattr(meter_provider, "shutdown", None)
        if callable(meter_shutdown):
            meter_shutdown()
    except Exception:
        logger.debug("shutdown_tracing: cleanup failed", exc_info=True)
    finally:
        if os.environ.get(_PROCESS_GUARD_ENV) == str(os.getpid()):
            os.environ.pop(_PROCESS_GUARD_ENV, None)
        _INITIALIZED = False


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


@contextmanager
def traced_root(name: str, **attrs: Any) -> Iterator[Span]:
    """Start a span as a new trace root, ignoring any active parent context."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name, context=_otel_context.Context()) as span:
        if attrs:
            set_span_attributes(span, **attrs)
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
    Also writes the plain 32-hex ``trace_id`` for UI/search surfaces.
    """
    propagate.inject(carrier)
    span = trace.get_current_span()
    ctx = span.get_span_context() if span is not None else trace.INVALID_SPAN_CONTEXT
    if ctx.trace_id:
        carrier["trace_id"] = format(ctx.trace_id, "032x")


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


def screenshot_analysis_duration_histogram() -> Histogram:
    """Per-frame analysis duration, partitioned by detected ``node``."""
    h = _METRICS_CACHE.get("screenshot_analysis_duration")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.screenshot.analysis.duration",
            unit="s",
            description=(
                "Wall-clock time spent analyzing one captured screenshot "
                "(screen detection + overlay analysis)."
            ),
        )
        _METRICS_CACHE["screenshot_analysis_duration"] = h
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


def redis_command_counter() -> Counter:
    """Number of Redis commands issued, partitioned by ``command``/``component``/``outcome``.

    ``command`` is the first token of the wire command (``GET``, ``HSET``,
    ``ZADD``, …). ``component`` identifies the producer (``scheduler``,
    ``worker``, ``ui``, …) so dashboards can split traffic by role.
    """
    c = _METRICS_CACHE.get("redis_command_count")
    if c is None:
        c = get_meter().create_counter(
            name="wos.redis.command.count",
            description="Redis commands issued from any wos client.",
        )
        _METRICS_CACHE["redis_command_count"] = c
    return c


def redis_command_duration_histogram() -> Histogram:
    """Wall-clock latency of a single Redis command, tagged like the counter."""
    h = _METRICS_CACHE.get("redis_command_duration")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.redis.command.duration",
            unit="s",
            description="Wall-clock time spent waiting on a Redis command response.",
        )
        _METRICS_CACHE["redis_command_duration"] = h
    return h


def recent_runs_history_age_histogram() -> Histogram:
    """Age in seconds of the oldest entry in the per-instance ``recent_runs`` ZSET.

    Tagged by ``instance_id``. Sampled once per scheduler tick. Tells us
    how far back history reaches — when the value plateaus far below
    ``RECENT_RUNS_RETENTION_SECONDS``, the count cap is binding (long-interval
    cron specs may fall out of history before they next fire) and bumping
    ``RECENT_RUNS_RETENTION_CAP`` is the lever.
    """
    h = _METRICS_CACHE.get("recent_runs_history_age")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.scheduler.recent_runs.history_age",
            unit="s",
            description="Age of the oldest recent_runs entry, per instance.",
        )
        _METRICS_CACHE["recent_runs_history_age"] = h
    return h


def recent_runs_history_size_histogram() -> Histogram:
    """Number of entries currently in the per-instance ``recent_runs`` ZSET.

    Companion to :func:`recent_runs_history_age_histogram` — together they
    say "100 entries spanning 6 hours". Sampled once per scheduler tick.
    """
    h = _METRICS_CACHE.get("recent_runs_history_size")
    if h is None:
        h = get_meter().create_histogram(
            name="wos.scheduler.recent_runs.history_size",
            description="Number of entries in recent_runs ZSET, per instance.",
        )
        _METRICS_CACHE["recent_runs_history_size"] = h
    return h


def overlay_push_scenario_counter() -> Counter:
    """Counter of overlay-driven ``pushScenario`` attempts.

    Partitioned by ``scenario`` (target task_type), ``screen`` (current_screen
    / set_node at push time), ``region`` (analyzer rule region), and
    ``outcome`` (``enqueued``, ``throttled_push_ttl``, ``time_throttle``,
    ``disabled``, ``no_active_player``, ``dup_main_city``). Lets us spot
    pushes that fire often per screen — primary signal for adding a
    ``ttl:`` self-throttle to the rule.
    """
    c = _METRICS_CACHE.get("overlay_push_scenario_count")
    if c is None:
        c = get_meter().create_counter(
            name="wos.overlay.push_scenario.count",
            description="Overlay-driven pushScenario attempts by scenario/screen/region/outcome.",
        )
        _METRICS_CACHE["overlay_push_scenario_count"] = c
    return c


def trace_id_hex_for_history(
    *,
    span_ctx: trace.SpanContext | None = None,
    carrier: dict[str, Any] | None = None,
    fallback_seed: str = "",
) -> str:
    """Resolve a 32-hex trace id for queue history rows.

    Prefer the active OTel span, then W3C fields on ``carrier``, then a
    deterministic local id when the SDK is a no-op (so the dashboard still
    has a copyable correlation key).
    """
    from config.w3c_traceparent import trace_id_hex_from_carrier

    ctx = span_ctx or trace.INVALID_SPAN_CONTEXT
    if ctx.trace_id:
        return format(ctx.trace_id, "032x")
    tid = trace_id_hex_from_carrier(carrier)
    if tid:
        return tid
    seed = str(fallback_seed or "").strip()
    if seed:
        import hashlib

        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return ""


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
