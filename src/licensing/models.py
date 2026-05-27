"""Typed claims + error/status shapes for the license gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


class LicenseError(Exception):
    """Raised on any verification failure (bad signature, expired, wrong machine)."""

    def __init__(self, reason: str, code: str = "invalid") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(slots=True)
class LicenseClaims:
    """Validated payload of a license JWT.

    Mirrors the JWT body. ``raw`` keeps the unparsed dict for forward-compat —
    if the issuer adds a field the bot doesn't know about yet, it's not lost.
    """

    sub: str                       # user email or stable id
    machine_id: str                # bound host fingerprint
    tier: str                      # trial / pro / enterprise, etc.
    features: list[str] = field(default_factory=list)
    max_devices: int = 1
    max_players_per_device: int = 3
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    jti: str | None = None
    raw: dict[str, object] = field(default_factory=dict)

    def days_until_expiry(self) -> float | None:
        if self.expires_at is None:
            return None
        delta = self.expires_at - datetime.now(UTC)
        return delta.total_seconds() / 86400.0

    def has_feature(self, name: str) -> bool:
        return name in self.features


@dataclass(slots=True)
class LicenseStatus:
    """Status snapshot for the UI / API."""

    active: bool
    state: str                     # "active" | "missing" | "expired" | "invalid" | "machine_mismatch"
    reason: str | None = None
    sub: str | None = None
    tier: str | None = None
    features: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    days_left: float | None = None
    machine_id: str | None = None  # current host fingerprint (always populated)
    max_devices: int | None = None
    max_players_per_device: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "state": self.state,
            "reason": self.reason,
            "sub": self.sub,
            "tier": self.tier,
            "features": list(self.features),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "days_left": self.days_left,
            "machine_id": self.machine_id,
            "max_devices": self.max_devices,
            "max_players_per_device": self.max_players_per_device,
        }
