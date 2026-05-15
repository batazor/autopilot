from __future__ import annotations

from config.env_loader import load_env_once
from config.logging_otel import setup_otel_logging
from config.logging_stdout import setup_stdout_logging
from config.tracing import setup_tracing


def bootstrap_runtime_observability(
    component: str,
    *,
    instance_id: str | None = None,
) -> None:
    """Load process env and attach stdout + optional OTel telemetry."""
    load_env_once()
    setup_stdout_logging()
    setup_tracing(component, instance_id=instance_id)
    setup_otel_logging(component, instance_id=instance_id)
