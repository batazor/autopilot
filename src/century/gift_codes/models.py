"""Gift code data models — shared across WOS and Kingshot redeemers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RedeemStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    ALREADY_RECEIVED = "ALREADY_RECEIVED"
    CDK_EXPIRED = "CDK_EXPIRED"
    CDK_NOT_FOUND = "CDK_NOT_FOUND"
    STOVE_LEVEL_TOO_LOW = "STOVE_LEVEL_TOO_LOW"
    VIP_LEVEL_TOO_LOW = "VIP_LEVEL_TOO_LOW"
    FAILED = "FAILED"


class GiftCode(BaseModel):
    name: str
    game: str = "wos"
    expires: datetime | None = None
    user_for: dict[str, RedeemStatus] = Field(default_factory=dict, alias="userFor")
    last_api_err_code: int | None = Field(default=None, alias="lastApiErrCode")
    last_api_msg: str | None = Field(default=None, alias="lastApiMsg")

    model_config = {"populate_by_name": True, "extra": "allow"}

    def uses_calendar_expiry(self) -> bool:
        """Whether ``expires`` should make the code terminal without an API check."""
        return self.game != "kingshot"

    def is_expired(self) -> bool:
        if not self.uses_calendar_expiry():
            return False
        if self.expires is None:
            return False
        now = datetime.now(tz=UTC)
        expires = self.expires
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires.year < 2000:
            return False
        return now > expires

    def is_api_slot_dead(self) -> bool:
        """API said the code is gone or no longer valid (any known player row)."""
        return any(
            s in (RedeemStatus.CDK_EXPIRED, RedeemStatus.CDK_NOT_FOUND)
            for s in self.user_for.values()
        )

    def is_effectively_expired(self) -> bool:
        return self.is_expired() or self.is_api_slot_dead()

    def needs_redemption(self, player_id: str) -> bool:
        if self.is_effectively_expired():
            return False
        status = self.user_for.get(player_id)
        return status not in (
            RedeemStatus.SUCCESS,
            RedeemStatus.ALREADY_RECEIVED,
            RedeemStatus.CDK_EXPIRED,
            RedeemStatus.CDK_NOT_FOUND,
            RedeemStatus.STOVE_LEVEL_TOO_LOW,
            RedeemStatus.VIP_LEVEL_TOO_LOW,
        )


class GiftCodeDB(BaseModel):
    """Aggregate of GiftCode entries. Kept as a public alias for callers /
    tests that want a single typed container around a code list."""
    codes: list[GiftCode] = []
