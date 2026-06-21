"""Ship ``stdlib logging`` records to Grafana Cloud Loki via OTLP/HTTP.

The OTel SDK Logs API piggybacks on the existing ``OTEL_EXPORTER_OTLP_ENDPOINT``
that already gates trace export (see :mod:`config.tracing`). Grafana Cloud
splits OTLP signals server-side, routing logs to Loki and traces to Tempo with
no extra client config.

Behaviour:

* No ``OTEL_EXPORTER_OTLP_ENDPOINT`` → no-op. Stdout logging stays the only sink.
* ``OTEL_LOGS_EXPORTER=none`` → no-op (operator can disable logs while keeping
  traces).
* Endpoint configured → attach a batching ``LoggingHandler`` to the root logger
  at ``WOS_OTEL_LOG_LEVEL`` (default ``INFO``). Stdout handler is left alone, so
  DEBUG keeps printing locally while only INFO+ is shipped to the cloud.

Log records carry the same ``inst`` / ``player`` / ``node`` / ``scenario``
context-vars that :class:`config.log_context.LogContextFilter` populates on the
stdout side. They land in the OTLP record as structured attributes (Grafana
Cloud's OTLP integration promotes these to Loki labels when configured per its
operator UI; otherwise they remain searchable as line fields).

OpenTelemetry SDK setup is idempotent: a module-level flag short-circuits a
second call, so each entry-point can call this freely without worrying about
fork/spawn re-imports stacking duplicate handlers.
"""
from __future__ import annotations

import logging
import os
import socket
from importlib import metadata as _md
from typing import Any

logger = logging.getLogger(__name__)


_INITIALIZED = False
_HANDLER: logging.Handler | None = None
_PROCESS_GUARD_ENV = "WOS_OTEL_LOGGING_INITIALIZED_PID"


def _is_truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _project_version() -> str:
    try:
        return _md.version("autopilot")
    except _md.PackageNotFoundError:
        return "0.0.0"


def _resolve_level() -> int:
    raw = (os.environ.get("WOS_OTEL_LOG_LEVEL") or "INFO").strip().upper()
    if raw.isdigit():
        return int(raw)
    return logging.getLevelName(raw) if raw else logging.INFO


class _OtelLogAttrsFilter(logging.Filter):
    """Lift our context attributes from the record into ``extra``-style attrs.

    ``LogContextFilter`` (config/log_context.py) already sets ``record.inst`` /
    ``player`` / ``node`` / ``scenario``. OTel's ``LoggingHandler`` serialises
    any non-stdlib ``LogRecord`` attribute into the OTLP log record's
    ``attributes`` map, so these end up as searchable structured fields in
    Loki. Empty / placeholder values ("" / "-") are skipped so we don't pollute
    the index with low-signal entries.
    """

    _CTX_KEYS = ("inst", "player", "node", "scenario")
    _SKIP_VALUES = frozenset({"", "-"})

    def filter(self, record: logging.LogRecord) -> bool:
        for key in self._CTX_KEYS:
            val = getattr(record, key, None)
            if val is None or val in self._SKIP_VALUES:
                continue
            # ``wos.*`` namespace mirrors the trace-attribute naming so a
            # single Grafana search ("wos.scenario=...") works across signals.
            setattr(record, f"wos.{key}", str(val))
        return True


def setup_otel_logging(
    component: str,
    *,
    instance_id: str | None = None,
    level: int | None = None,
) -> None:
    """Attach an OTLP logging handler to the root logger.

    Safe to call multiple times — second call is a no-op.

    Args:
        component: short role label — ``supervisor`` / ``scheduler`` /
            ``worker`` / ``cli`` / ``ui``. All processes share
            ``service.name="wos"``; the role lives in ``wos.component`` so
            Loki streams stay grouped per service while remaining filterable
            by role (matches ``config.tracing.setup_tracing``).
        instance_id: OTel resource ``service.instance.id``. Defaults to
            ``socket.gethostname()``.
        level: Minimum stdlib level shipped to Loki. ``None`` reads
            ``WOS_OTEL_LOG_LEVEL`` (default ``INFO``).
    """
    global _INITIALIZED, _HANDLER
    current_pid = str(os.getpid())
    if _INITIALIZED or os.environ.get(_PROCESS_GUARD_ENV) == current_pid:
        _INITIALIZED = True
        if _HANDLER is not None and _HANDLER not in logging.getLogger().handlers:
            logging.getLogger().addHandler(_HANDLER)
        return

    if _is_truthy_env("OTEL_SDK_DISABLED"):
        return
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip():
        return
    if (os.environ.get("OTEL_LOGS_EXPORTER") or "").strip().lower() == "none":
        return

    # Imports kept inside the function so projects without the SDK installed
    # (or with it explicitly disabled) don't pay the import cost.
    #
    # NOTE: the ``LoggingHandler`` shipped in ``opentelemetry.sdk._logs`` is
    # deprecated since OTel SDK 1.40+ in favour of the one in
    # ``opentelemetry-instrumentation-logging``. The two have the same
    # constructor shape (``level`` + ``logger_provider``) plus the new variant
    # takes ``log_code_attributes=True`` to emit ``code.file.path`` /
    # ``code.function.name`` / ``code.line.number`` on every record — useful
    # for jumping from a Loki log line straight to the source location.
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create(
        {
            "service.name": "wos",
            "service.namespace": os.environ.get("OTEL_SERVICE_NAMESPACE") or "wos",
            "service.instance.id": instance_id or socket.gethostname(),
            "service.version": _project_version(),
            "wos.component": component,
        }
    )

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)

    effective_level = level if level is not None else _resolve_level()
    # ``log_code_attributes=True`` adds ``code.file.path`` / ``code.function.name``
    # / ``code.line.number`` to every shipped log so Loki can deep-link to source.
    handler = LoggingHandler(
        level=effective_level,
        logger_provider=provider,
        log_code_attributes=True,
    )
    handler.addFilter(_OtelLogAttrsFilter())
    # Import here to avoid a cycle: log_context imports nothing heavy, but the
    # stdout module imports tracing which imports this module on some paths.
    from config.log_context import LogContextFilter

    handler.addFilter(LogContextFilter())

    logging.getLogger().addHandler(handler)
    _HANDLER = handler
    _INITIALIZED = True
    os.environ[_PROCESS_GUARD_ENV] = current_pid

    logger.info(
        "OTel logging enabled — component=%s instance=%s level=%s endpoint=%s",
        component,
        instance_id or socket.gethostname(),
        logging.getLevelName(effective_level),
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )


def shutdown_otel_logging() -> None:
    """Flush + detach the OTLP handler. Safe to call when never initialised."""
    global _INITIALIZED, _HANDLER
    if not _INITIALIZED or _HANDLER is None:
        return
    try:
        logging.getLogger().removeHandler(_HANDLER)
        _HANDLER.close()
    except Exception:
        logger.debug("shutdown_otel_logging: cleanup failed", exc_info=True)
    finally:
        if os.environ.get(_PROCESS_GUARD_ENV) == str(os.getpid()):
            os.environ.pop(_PROCESS_GUARD_ENV, None)
        _HANDLER = None
        _INITIALIZED = False


def _reset_for_tests() -> None:
    """Force re-init on the next ``setup_otel_logging`` call. Test-only."""
    shutdown_otel_logging()


def _otel_logging_handler_for_tests() -> Any:
    """Expose the active handler so tests can introspect it."""
    return _HANDLER
