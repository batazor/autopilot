"""Auto-load ``.env`` from the repo root at process startup.

Idempotent: a module-global flag short-circuits the second call so re-imports
in spawned multiprocessing children don't re-parse the file (it's cheap, but
worth keeping clean).

Search strategy: walk upwards from this module's directory looking for the
first ``.env`` file. That makes the loader independent of the caller's
``cwd`` — supervisor / scheduler / worker entry points all resolve to the
same repo root regardless of how the process was invoked.

``override=False`` is the load mode so values explicitly set via the OS env
(docker-compose ``environment:``, CI runners, ``OTEL_SDK_DISABLED=true`` for
a one-off debug session) take precedence over what's in ``.env``. The dev
file is the *fallback*, not the source of truth.

After ``.env`` is loaded, :func:`apply_otel_env_defaults` wires OTLP export to
the Grafana Cloud gateway when ``GRAFANA_CLOUD_STACK_ID`` and
``GRAFANA_CLOUD_API_TOKEN`` are present.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


_LOADED = False


def _find_dotenv() -> Path | None:
    """Walk up from this file to the first ancestor containing ``.env``."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_once() -> None:
    """Load ``.env`` from the repo root once per process. No-op if missing."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True  # claim the slot before any work so re-entrant calls bail.

    dotenv_path = _find_dotenv()
    if dotenv_path is not None:
        try:
            from dotenv import load_dotenv
        except ImportError:
            logger.debug(
                "python-dotenv not installed — skipping .env auto-load (install "
                "with ``uv sync`` to enable)."
            )
        else:
            load_dotenv(dotenv_path, override=False)
            logger.debug("Loaded environment from %s", dotenv_path)

    apply_otel_env_defaults()


_LOCAL_ALLOY_ENDPOINTS = frozenset({
    "http://localhost:4318",
    "http://127.0.0.1:4318",
})


def _is_truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _grafana_otlp_endpoint() -> str:
    region = (os.environ.get("GRAFANA_CLOUD_OTLP_REGION") or "eu-west-2").strip()
    return f"https://otlp-gateway-prod-{region}.grafana.net/otlp"


def _grafana_otlp_headers(stack_id: str, api_token: str) -> str:
    basic = base64.b64encode(f"{stack_id}:{api_token}".encode()).decode("ascii")
    return f"Authorization=Basic {basic}"


def apply_otel_env_defaults() -> None:
    """Fill OTLP endpoint/headers from Grafana Cloud credentials when appropriate.

  * ``OTEL_SDK_DISABLED=true`` → no changes.
  * Explicit ``OTEL_EXPORTER_OTLP_HEADERS`` → never overwrite (operator owns auth).
  * ``GRAFANA_CLOUD_STACK_ID`` + ``GRAFANA_CLOUD_API_TOKEN`` set → gateway URL +
    Basic auth unless the operator already pointed OTLP somewhere other than the
    template localhost default.
  * No Grafana creds and endpoint unset → telemetry stays off (tracing/logging
    modules treat missing endpoint as a no-op).
    """
    if _is_truthy_env("OTEL_SDK_DISABLED"):
        return

    stack_id = (os.environ.get("GRAFANA_CLOUD_STACK_ID") or "").strip()
    api_token = (os.environ.get("GRAFANA_CLOUD_API_TOKEN") or "").strip()
    endpoint = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    headers = (os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") or "").strip()

    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    if not stack_id or not api_token:
        return

    if not headers:
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_HEADERS",
            _grafana_otlp_headers(stack_id, api_token),
        )

    # Upgrade template localhost (no collector running) or empty endpoint.
    if not endpoint or endpoint in _LOCAL_ALLOY_ENDPOINTS:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = _grafana_otlp_endpoint()
