"""License feature gate — thin wrappers around :func:`license_status`.

Used by Pro features (gift-codes external accounts, etc.) at three layers:

  - **API**: ``require_feature`` in route handlers → returns 402.
  - **Service layer**: ``has_feature`` short-circuits to read-only or no-op.
  - **UI**: serializes the feature list in ``/api/license/status`` so the front
    end can grey out controls.

Lookup is cheap (verify is JWT decode + signature) and not cached here —
``license_status`` re-verifies every call. Callers that need to hot-loop
should cache the boolean themselves for the request's lifetime.
"""

from __future__ import annotations

from licensing.models import LicenseError
from licensing.status import license_status


def has_feature(name: str) -> bool:
    """True iff the license is active and includes ``name`` in ``features``."""
    status = license_status()
    return bool(status.active and name in (status.features or []))


def external_accounts_limit() -> int:
    """Per-game cap on external gift-code accounts for the active license.

    Returns 0 when no license is active (callers should treat 0 as "blocked").
    The cap is independent of the feature flag, but in practice a tier without
    the external-accounts feature also carries a 0 cap.
    """
    status = license_status()
    if not status.active:
        return 0
    return int(status.max_external_accounts or 0)


def require_feature(name: str) -> None:
    """Raise :class:`LicenseError` if ``name`` is not licensed.

    Catch this in API routes to translate to HTTP 402 Payment Required with
    ``reason='feature_not_licensed'``.
    """
    if has_feature(name):
        return
    status = license_status()
    if not status.active:
        msg = f"feature {name!r} requires an active license (current state: {status.state})"
        raise LicenseError(msg, code="feature_locked")
    msg = f"feature {name!r} is not included in your license tier ({status.tier!r})"
    raise LicenseError(msg, code="feature_locked")
