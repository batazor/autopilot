from __future__ import annotations

import os

from config.crash_logging import install_crash_logging
from config.env_loader import load_env_once
from config.logging_otel import setup_otel_logging
from config.logging_stdout import setup_stdout_logging
from config.profiling import setup_profiling
from config.telemetry import setup_telemetry_metrics
from config.tracing import setup_tracing


def _apply_baked_telemetry_secrets() -> None:
    """Force telemetry export through the baked Grafana Cloud OTLP endpoint.

    The maintainer's production Docker build ships real Grafana Cloud creds in
    ``src/config/_telemetry_secrets.py`` (gitignored, dropped in before the
    build). At runtime we read those constants and *overwrite* the OTel SDK's
    standard env vars — so
    any user-side attempt to disable export (``OTEL_SDK_DISABLED=true``) or
    divert it (``OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost/sink``) is
    nullified before :func:`setup_tracing` reads the env.

    When the public-repo build runs without baked secrets, this function is
    a no-op and the OTel SDK stays in its default off state — exactly the
    behaviour we want for development.
    """
    try:
        from config import _telemetry_secrets as secrets
    except ImportError:
        # Public-repo build with no secrets file — telemetry not enforced.
        # Dev / tests skip the entire OTel pipeline by default.
        return

    endpoint = (getattr(secrets, "ENDPOINT", "") or "").strip()
    auth = (getattr(secrets, "AUTH_HEADER", "") or "").strip()
    if not endpoint:
        # Secrets file present but empty (e.g. maintainer's local dev) —
        # treat same as missing file.
        return

    # Unconditional overwrite: end users cannot point telemetry elsewhere
    # by pre-setting these env vars in their shell, docker-compose, or .env.
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    if auth:
        # OTel SDK reads the header as ``Key=Value,Key=Value``.
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={auth}"
    # ``OTEL_SDK_DISABLED=true`` would short-circuit setup_tracing before it
    # touches our forced endpoint. Strip it so the off-switch can't be used
    # against a production build.
    os.environ.pop("OTEL_SDK_DISABLED", None)
    # Metrics is the signal that carries our user-facing telemetry — make
    # absolutely sure the user can't disable it via the per-signal opt-out.
    os.environ.pop("OTEL_METRICS_EXPORTER", None)
    # Logs are *forced on*, at ERROR only: ship uncaught exceptions / crashes and
    # ERROR-level lines (with stack traces) to Loki so the maintainer can debug
    # field failures. Clearing any user-set ``OTEL_LOGS_EXPORTER`` lets the SDK
    # default (otlp) apply, and pinning the level keeps volume bounded and stops
    # a user from flooding the backend with DEBUG. Requires the baked Grafana
    # Cloud token to carry ``logs:write`` scope.
    os.environ.pop("OTEL_LOGS_EXPORTER", None)
    os.environ["WOS_OTEL_LOG_LEVEL"] = "ERROR"
    # Traces stay off — the metrics-only policy doesn't carry ``traces:write``,
    # and we don't ship per-span data from user machines.
    os.environ["OTEL_TRACES_EXPORTER"] = "none"


def bootstrap_runtime_observability(
    component: str,
    *,
    instance_id: str | None = None,
) -> None:
    """Load process env and attach stdout + optional OTel telemetry + Pyroscope profiling."""
    load_env_once()
    setup_stdout_logging()
    # Inject baked-in OTLP creds *before* setup_tracing — that function reads
    # ``OTEL_EXPORTER_OTLP_ENDPOINT`` and bails out (no-op tracer) when empty.
    _apply_baked_telemetry_secrets()
    setup_tracing(component, instance_id=instance_id)
    setup_otel_logging(component, instance_id=instance_id)
    # Ship uncaught exceptions / crashes through logging so they reach Loki too.
    install_crash_logging()
    # The user-facing telemetry gauges live in the MeterProvider that
    # ``setup_tracing`` just installed (or didn't, in which case they
    # silently no-op).
    setup_telemetry_metrics()
    # Profiling runs last so :func:`setup_profiling` can see the active
    # TracerProvider and wire pyroscope-otel's span processor onto it.
    setup_profiling(component, instance_id=instance_id)


def shutdown_runtime_observability() -> None:
    """Stop OTel exporters before process exit (avoids noisy atexit on Ctrl+C)."""
    from config.logging_otel import shutdown_otel_logging
    from config.tracing import shutdown_tracing

    shutdown_otel_logging()
    shutdown_tracing()
