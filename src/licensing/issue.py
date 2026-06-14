"""JWT minting (developer-side). Only callable where the private key is present."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from licensing.keys import load_private_key
from licensing.plans import external_accounts_limit_for_tier
from licensing.verify import ALGORITHM, ISSUER

_MAX_DAYS = 365


def issue_license(
    *,
    sub: str,
    machine_id: str,
    days: int = 30,
    tier: str = "pro",
    max_devices: int = 1,
    max_players_per_device: int = 3,
    max_external_accounts: int | None = None,
    issued_at: datetime | None = None,
) -> tuple[str, dict[str, object]]:
    """Sign and return ``(token, payload)``.

    Caller-side validation (kept here, not in the CLI, so the API issuer can reuse it):
    - ``sub`` and ``machine_id`` must be non-empty
    - ``days`` clamped to ``[1, 365]``
    - ``max_devices`` clamped to ``[1, 100]``
    - ``max_external_accounts=None`` resolves the per-game cap from the plan
      catalog for ``tier``; an explicit int is clamped to ``[0, 1000]``
    """
    sub = (sub or "").strip()
    machine_id = (machine_id or "").strip()
    if not sub:
        msg = "sub (user identifier) is required"
        raise ValueError(msg)
    if not machine_id:
        msg = "machine_id is required"
        raise ValueError(msg)

    days = max(1, min(int(days), _MAX_DAYS))
    max_devices = max(1, min(int(max_devices), 100))
    max_players_per_device = max(1, min(int(max_players_per_device), 100))
    resolved_external = (
        external_accounts_limit_for_tier(tier)
        if max_external_accounts is None
        else max(0, min(int(max_external_accounts), 1000))
    )

    now = issued_at or datetime.now(UTC)
    expires = now + timedelta(days=days)
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
        "machine_id": machine_id,
        "tier": tier,
        "max_devices": max_devices,
        "max_players_per_device": max_players_per_device,
        "max_external_accounts": resolved_external,
    }

    private_key = load_private_key()
    token = jwt.encode(payload, private_key, algorithm=ALGORITHM)
    return token, payload
