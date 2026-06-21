"""Per-user telemetry: heartbeat + uptime + workers + restart counter.

Sits on top of the OTel setup in :mod:`config.tracing` — that module already
configures the OTLP HTTP exporter and MeterProvider; this one just defines
the user-facing instruments and supplies their callbacks.

Why observable gauges (not regular gauges):
    The classic "active hosts right now" pattern needs a metric that arrives
    once per export cycle for every running bot. Observable gauges fit
    naturally — the OTel SDK invokes the callback at each export, so the
    series stays alive while the process is up and goes stale when it dies.
    Grafana's ``count(count by(host) (autopilot_heartbeat[5m]))`` then yields
    "distinct hosts seen in the last 5 minutes".

Bind-then-setup ordering:
    The supervisor calls :func:`bind_supervisor` after the supervisor instance
    exists, then :func:`setup_telemetry_metrics` registers the gauges with the
    meter. Callbacks read the bound state lazily, so they pick up later changes
    (e.g., worker count fluctuating as children restart) without the gauges
    being re-registered.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics

if TYPE_CHECKING:
    from collections.abc import Iterable

    from opentelemetry.metrics import CallbackOptions, Observation

    from worker.supervisor import Supervisor

logger = logging.getLogger(__name__)

_METER_NAME = "wos.telemetry"
_HEARTBEAT_VALUE = 1  # any non-zero constant — what matters is the timestamp + labels


# ---------------------------------------------------------------------------
# Bound state (set by supervisor at boot)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "start_time": time.time(),
    "supervisor": None,             # Supervisor | None
    "registered": False,            # guard so setup is idempotent across forks
}


def bind_supervisor(supervisor: Supervisor) -> None:
    """Called after the supervisor instance is created. Used by workers_active gauge."""
    _state["supervisor"] = supervisor


def reset_start_time() -> None:
    """Re-anchor uptime — useful in tests; not called in production."""
    _state["start_time"] = time.time()


# ---------------------------------------------------------------------------
# Attribute composition
# ---------------------------------------------------------------------------


def _common_attributes() -> dict[str, str]:
    """The single label every gauge carries: the host running this bot.

    Minimised to ``host`` only — that's enough to answer "which machines are
    online" and keeps cardinality at exactly *one series per host per metric*.
    """
    return {"host": socket.gethostname() or "unknown"}


# ---------------------------------------------------------------------------
# Observable-gauge callbacks
# ---------------------------------------------------------------------------


def _heartbeat_cb(_options: CallbackOptions) -> Iterable[Observation]:
    from opentelemetry.metrics import Observation as _Obs

    return [_Obs(value=_HEARTBEAT_VALUE, attributes=_common_attributes())]


def _uptime_cb(_options: CallbackOptions) -> Iterable[Observation]:
    from opentelemetry.metrics import Observation as _Obs

    uptime = time.time() - _state["start_time"]
    return [_Obs(value=uptime, attributes=_common_attributes())]


def _workers_active_cb(_options: CallbackOptions) -> Iterable[Observation]:
    from opentelemetry.metrics import Observation as _Obs

    supervisor = _state.get("supervisor")
    # ``Supervisor`` holds a ``_processes`` dict — count alive worker children
    # (excluding the scheduler so the metric reflects emulator-attached
    # workers, which is what "how many bots are running" really means).
    if supervisor is None:
        return []
    count = 0
    try:
        for name, proc in supervisor._processes.items():
            if name == "scheduler":
                continue
            if proc.is_alive():
                count += 1
    except Exception:
        # Process polling can race against shutdown; never raise from a callback.
        logger.debug("workers_active callback failed", exc_info=True)
        return []
    return [_Obs(value=count, attributes=_common_attributes())]


# ---------------------------------------------------------------------------
# Counters (synchronous — called from the supervisor when events happen)
# ---------------------------------------------------------------------------


_counters: dict[str, Any] = {}


def _get_meter() -> metrics.Meter:
    return metrics.get_meter(_METER_NAME)


def _restart_counter() -> metrics.Counter:
    c = _counters.get("restarts")
    if c is None:
        c = _get_meter().create_counter(
            name="autopilot.restarts",
            description="Worker / scheduler restarts triggered by the supervisor.",
        )
        _counters["restarts"] = c
    return c


def report_restart(name: str, *, attempt: int) -> None:
    """Called by the supervisor when a worker/scheduler is restarted after death.

    Carries ``process_name`` only — a fleet-wide aggregate answers the
    actual question ("are bots crash-looping right now?"). Per-user
    attribution would multiply series by N users with no analytical gain
    on the restart-rate panel.
    """
    del attempt  # documented unused; kept in signature so callers stay stable
    try:
        _restart_counter().add(1, attributes={"process_name": name})
    except Exception:
        logger.debug("report_restart failed", exc_info=True)


# ---------------------------------------------------------------------------
# Setup — register gauges with the MeterProvider
# ---------------------------------------------------------------------------


def setup_telemetry_metrics() -> None:
    """Register observable gauges. Idempotent; safe across spawned subprocesses.

    Call this AFTER :func:`config.tracing.setup_tracing` has installed the
    MeterProvider; calling earlier registers the gauges with the no-op API
    meter and they never export.
    """
    if _state["registered"]:
        return
    # When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset the API returns a no-op
    # meter that silently accepts ``create_observable_gauge``. We still flip
    # ``registered`` so we don't re-register if the env arrives later.
    meter = _get_meter()
    meter.create_observable_gauge(
        name="autopilot.heartbeat",
        callbacks=[_heartbeat_cb],
        description=(
            "Constant 1 emitted every export interval — count distinct "
            "``host`` over a recent window to see active machines."
        ),
    )
    meter.create_observable_gauge(
        name="autopilot.uptime_seconds",
        callbacks=[_uptime_cb],
        unit="s",
        description="Seconds since the supervisor process started.",
    )
    meter.create_observable_gauge(
        name="autopilot.workers.active",
        callbacks=[_workers_active_cb],
        description="Number of alive worker subprocesses (scheduler excluded).",
    )
    _state["registered"] = True
    logger.info(
        "telemetry: gauges registered (endpoint=%s)",
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "<unset>",
    )
