"""Pyroscope continuous-profiling bootstrap.

Mirrors the contract of :mod:`config.tracing`: idempotent, env-driven SDK
setup so every process (supervisor, scheduler, per-instance worker, CLI)
calls :func:`setup_profiling` once at boot and gets CPU profiles shipped
to whichever Pyroscope server the operator configured.

When ``PYROSCOPE_SERVER_ADDRESS`` is unset (or ``PYROSCOPE_DISABLED=true``),
the call is a no-op — no agent thread is started, no network traffic, no
import cost beyond this module itself.

Env-vars (read directly here; we map them onto ``pyroscope.configure``):

* ``PYROSCOPE_SERVER_ADDRESS`` — collector URL (e.g.
  ``https://profiles-prod-001.grafana.net``). When empty / unset, the
  function returns without configuring the agent.
* ``PYROSCOPE_BASIC_AUTH_USERNAME`` / ``PYROSCOPE_BASIC_AUTH_PASSWORD`` —
  HTTP basic auth (Grafana Cloud uses ``<instance_id>`` / ``<api_token>``).
* ``PYROSCOPE_TENANT_ID`` — optional tenant header (multi-tenant deploys).
* ``PYROSCOPE_SAMPLE_RATE`` — agent sample rate in Hz (default 100).
* ``PYROSCOPE_APPLICATION_NAME`` — override the default ``wos`` name.
* ``PYROSCOPE_DISABLED`` — off-switch that wins regardless of address.

All processes share ``application_name="wos"`` (matching the OTel
``service.name``); the role lives in the ``wos_component`` tag so the
Pyroscope UI's tag selector mirrors the TraceQL ``{wos.component="…"}``
filter without splitting the application list.

When :mod:`pyroscope_otel` is available *and* an OTel ``TracerProvider``
is already installed (i.e. :func:`config.tracing.setup_tracing` ran
first and a collector endpoint was configured), this module attaches a
:class:`PyroscopeSpanProcessor` so spans carry ``pyroscope.profile.id``
baggage and the Grafana "span profiles" UI can jump from a trace to the
matching flamegraph.
"""
from __future__ import annotations

import logging
import os
import socket
from importlib import metadata as _md

from opentelemetry import trace

logger = logging.getLogger(__name__)

_INITIALIZED = False
_PROCESS_GUARD_ENV = "WOS_PYROSCOPE_INITIALIZED_PID"


def _is_truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _project_version() -> str:
    try:
        return _md.version("whiteout-survival-autopilot")
    except _md.PackageNotFoundError:
        return "0.0.0"


def _attach_span_processor() -> bool:
    """Wire pyroscope-otel's span processor onto the active TracerProvider.

    Returns True if attachment succeeded, False when the optional
    ``pyroscope-otel`` package is missing or no real TracerProvider is
    installed (i.e. tracing is off, so there is nothing to correlate with).
    """
    try:
        from pyroscope.otel import PyroscopeSpanProcessor  # type: ignore[import-not-found]
    except ImportError:
        return False

    provider = trace.get_tracer_provider()
    add_span_processor = getattr(provider, "add_span_processor", None)
    if not callable(add_span_processor):
        # ``ProxyTracerProvider`` (no-op default) lacks this method —
        # tracing wasn't configured, so span profiles have nothing to
        # latch onto. Skip silently rather than crash.
        return False
    add_span_processor(PyroscopeSpanProcessor())
    return True


def setup_profiling(component: str, *, instance_id: str | None = None) -> None:
    """Initialize the Pyroscope agent for the calling process.

    Safe to call multiple times — second call is a no-op. After ``spawn``-ed
    multiprocessing children re-import this module, so each child must call
    this from its own entry point (the parent's agent thread does not
    propagate across the fork barrier).

    Args:
        component: short role label — ``supervisor``, ``scheduler``,
            ``worker``, ``cli``, ``ui``. Stamped onto every sample as the
            ``wos_component`` tag.
        instance_id: identifier for this process. Defaults to ``hostname``
            for non-worker processes; workers pass their BlueStacks id so
            each instance is distinguishable in the Pyroscope tag selector.
    """
    global _INITIALIZED
    current_pid = str(os.getpid())
    if _INITIALIZED or os.environ.get(_PROCESS_GUARD_ENV) == current_pid:
        _INITIALIZED = True
        return

    if _is_truthy_env("PYROSCOPE_DISABLED"):
        return
    server_address = (os.environ.get("PYROSCOPE_SERVER_ADDRESS") or "").strip()
    if not server_address:
        # No collector configured — keep the agent thread dormant.
        return

    try:
        import pyroscope  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "PYROSCOPE_SERVER_ADDRESS set but ``pyroscope-io`` is not installed — "
            "skipping profiling setup."
        )
        return

    resolved_instance_id = instance_id or socket.gethostname()
    application_name = (os.environ.get("PYROSCOPE_APPLICATION_NAME") or "wos").strip() or "wos"
    tags: dict[str, str] = {
        "wos_component": component,
        "service_namespace": os.environ.get("OTEL_SERVICE_NAMESPACE") or "wos",
        "service_instance_id": resolved_instance_id,
        "service_version": _project_version(),
    }

    configure_kwargs: dict[str, object] = {
        "application_name": application_name,
        "server_address": server_address,
        "tags": tags,
    }

    sample_rate_raw = (os.environ.get("PYROSCOPE_SAMPLE_RATE") or "").strip()
    if sample_rate_raw:
        try:
            configure_kwargs["sample_rate"] = int(sample_rate_raw)
        except ValueError:
            logger.warning("Ignoring invalid PYROSCOPE_SAMPLE_RATE=%r (expected int Hz)", sample_rate_raw)

    basic_auth_username = (os.environ.get("PYROSCOPE_BASIC_AUTH_USERNAME") or "").strip()
    basic_auth_password = (os.environ.get("PYROSCOPE_BASIC_AUTH_PASSWORD") or "").strip()
    if basic_auth_username and basic_auth_password:
        configure_kwargs["basic_auth_username"] = basic_auth_username
        configure_kwargs["basic_auth_password"] = basic_auth_password

    tenant_id = (os.environ.get("PYROSCOPE_TENANT_ID") or "").strip()
    if tenant_id:
        configure_kwargs["tenant_id"] = tenant_id

    pyroscope.configure(**configure_kwargs)

    span_profiles_attached = _attach_span_processor()

    _INITIALIZED = True
    os.environ[_PROCESS_GUARD_ENV] = current_pid
    logger.info(
        "Pyroscope profiling enabled — component=%s instance=%s server=%s span_profiles=%s",
        component,
        resolved_instance_id,
        server_address,
        "on" if span_profiles_attached else "off",
    )
