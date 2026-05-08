"""Gift code data models."""

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
    FAILED = "FAILED"


class GiftCode(BaseModel):
    name: str
    expires: datetime | None = None
    user_for: dict[str, RedeemStatus] = Field(default_factory=dict, alias="userFor")
    last_api_err_code: int | None = Field(default=None, alias="lastApiErrCode")
    last_api_msg: str | None = Field(default=None, alias="lastApiMsg")

    model_config = {"populate_by_name": True, "extra": "allow"}

    def is_expired(self) -> bool:
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
        )


class GiftCodeDB(BaseModel):
    codes: list[GiftCode] = []


def gift_code_to_yaml_dict(c: GiftCode) -> dict[str, object]:
    row: dict[str, object] = {
        "name": c.name,
        "userFor": {k: v.value for k, v in c.user_for.items()},
    }
    if c.expires is not None:
        row["expires"] = c.expires.isoformat()
    if c.last_api_err_code is not None:
        row["lastApiErrCode"] = c.last_api_err_code
    if c.last_api_msg is not None:
        row["lastApiMsg"] = c.last_api_msg
    return row


def gift_db_to_yaml_dict(db: GiftCodeDB) -> dict[str, object]:
    return {"codes": [gift_code_to_yaml_dict(c) for c in db.codes]}
