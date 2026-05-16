"""Gift code model and YAML helpers."""

from __future__ import annotations

import pytest
import yaml

models = pytest.importorskip(
    "modules.gift_codes.models",
    reason="gift_codes module is in draft (modules/draft/gift_codes/) — skip until promoted",
)
GiftCode = models.GiftCode
GiftCodeDB = models.GiftCodeDB
RedeemStatus = models.RedeemStatus
gift_code_to_yaml_dict = models.gift_code_to_yaml_dict
gift_db_to_yaml_dict = models.gift_db_to_yaml_dict


def test_is_effectively_expired_calendar() -> None:
    c = GiftCode(name="X", expires="2099-01-01T00:00:00Z")
    assert not c.is_effectively_expired()


def test_is_effectively_expired_api_cdk_expired() -> None:
    c = GiftCode(
        name="X",
        user_for={"1": RedeemStatus.CDK_EXPIRED},
    )
    assert c.is_effectively_expired()
    assert c.is_api_slot_dead()


def test_needs_redemption_respects_cdk_not_found() -> None:
    c = GiftCode(name="X", user_for={"1": RedeemStatus.CDK_NOT_FOUND})
    assert not c.needs_redemption("1")


def test_gift_code_yaml_roundtrip_includes_api_fields() -> None:
    db = GiftCodeDB(
        codes=[
            GiftCode(
                name="ABC",
                user_for={"9": RedeemStatus.SUCCESS},
                last_api_err_code=20000,
                last_api_msg="ok",
            )
        ]
    )
    dumped = yaml.safe_dump(gift_db_to_yaml_dict(db), allow_unicode=True)
    back = GiftCodeDB.model_validate(yaml.safe_load(dumped))
    assert back.codes[0].last_api_err_code == 20000
    assert back.codes[0].last_api_msg == "ok"
    assert back.codes[0].user_for["9"] == RedeemStatus.SUCCESS


def test_gift_code_to_yaml_dict_omits_empty_api() -> None:
    row = gift_code_to_yaml_dict(GiftCode(name="Z"))
    assert "lastApiErrCode" not in row
    assert "lastApiMsg" not in row
