"""Gift code data models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class RedeemStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    ALREADY_RECEIVED = "ALREADY_RECEIVED"
    CDK_EXPIRED = "CDK_EXPIRED"
    CDK_NOT_FOUND = "CDK_NOT_FOUND"
    FAILED = "FAILED"


class GiftCode(BaseModel):
    name: str
    expires: datetime | None = None
    user_for: dict[str, RedeemStatus] = {}

    model_config = {"populate_by_name": True, "extra": "allow"}

    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        from datetime import timezone
        now = datetime.now(tz=timezone.utc)
        expires = self.expires
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires.year < 2000:
            return False
        return now > expires

    def needs_redemption(self, player_id: str) -> bool:
        if self.is_expired():
            return False
        status = self.user_for.get(player_id)
        return status not in (
            RedeemStatus.SUCCESS,
            RedeemStatus.ALREADY_RECEIVED,
            RedeemStatus.CDK_EXPIRED,
        )


class GiftCodeDB(BaseModel):
    codes: list[GiftCode] = []
