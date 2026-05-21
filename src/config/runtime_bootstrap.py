from __future__ import annotations

from config.env_loader import load_env_once
from config.logging_otel import setup_otel_logging
from config.logging_stdout import setup_stdout_logging
from config.profiling import setup_profiling
from config.tracing import setup_tracing


def bootstrap_runtime_observability(
    component: str,
    *,
    instance_id: str | None = None,
) -> None:
    """Load process env and attach stdout + optional OTel telemetry + Pyroscope profiling."""
    load_env_once()
    setup_stdout_logging()
    setup_tracing(component, instance_id=instance_id)
    setup_otel_logging(component, instance_id=instance_id)
    # Profiling runs last so :func:`setup_profiling` can see the active
    # TracerProvider and wire pyroscope-otel's span processor onto it.
    setup_profiling(component, instance_id=instance_id)
