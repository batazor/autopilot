"""License tier gate — thin wrappers around :func:`license_status`.

Paid capabilities are gated by the subscription tier ladder ``r2 < r3 < r4``
(see ``licensing.plans``). A capability declares a minimum tier and callers
compare the active license against it at three layers:

  - **API**: ``require_tier`` in route handlers → returns 402 when too low.
  - **Service layer**: ``tier_active_at_least`` short-circuits to read-only or no-op.
  - **UI**: ``/api/license/status`` serializes the tier so the front end can
    grey out controls.

Lookup is cheap (verify is JWT decode + signature) and not cached here —
``license_status`` re-verifies every call. Callers that need to hot-loop
should cache the result themselves for the request's lifetime.
"""

from __future__ import annotations

from licensing.models import LicenseError
from licensing.plans import tier_at_least
from licensing.status import license_status


def current_tier() -> str | None:
    """The active license's tier id, or ``None`` when no license is active."""
    status = license_status()
    return status.tier if status.active else None


def tier_active_at_least(minimum: str) -> bool:
    """True iff a license is active and its tier ranks at/above ``minimum``."""
    status = license_status()
    return bool(status.active and tier_at_least(status.tier, minimum))


def external_accounts_limit() -> int:
    """Per-game cap on external gift-code accounts for the active license.

    Returns 0 when no license is active (callers should treat 0 as "blocked").
    The cap is independent of the tier gate, but in practice a tier below the
    external-accounts minimum also carries a 0 cap.
    """
    status = license_status()
    if not status.active:
        return 0
    return int(status.max_external_accounts or 0)


def require_tier(minimum: str) -> None:
    """Raise :class:`LicenseError` if the active tier is below ``minimum``.

    Catch this in API routes to translate to HTTP 402 Payment Required with
    ``reason='tier_too_low'``.
    """
    status = license_status()
    if not status.active:
        msg = f"this feature requires an active {minimum!r} license (current state: {status.state})"
        raise LicenseError(msg, code="tier_too_low")
    if not tier_at_least(status.tier, minimum):
        msg = (
            f"this feature requires tier {minimum!r} or higher "
            f"(your license tier: {status.tier!r})"
        )
        raise LicenseError(msg, code="tier_too_low")
