"""Ctrl+C during redemption must NOT mark codes as FAILED in ``db/giftCodes.yaml``.

Asyncio's default executor shuts down before mid-flight network calls finish,
surfacing as ``RuntimeError("cannot schedule new futures after shutdown")``.
The redeemer used to catch it as a generic exception and write FAILED — that
status then stuck in the codes db. The fix re-raises shutdown errors so the
caller's cancellation propagates cleanly.
"""

from __future__ import annotations

import pytest

_redeemer_mod = pytest.importorskip(
    "modules.gift_codes.redeemer",
    reason="gift_codes module is in draft (modules/draft/gift_codes/) — skip until promoted",
)
GiftCodeRedeemer = _redeemer_mod.GiftCodeRedeemer
_is_shutdown_error = _redeemer_mod._is_shutdown_error


def test_is_shutdown_error_recognises_executor_shutdown_message() -> None:
    assert _is_shutdown_error(RuntimeError("cannot schedule new futures after shutdown")) is True
    assert _is_shutdown_error(RuntimeError("Cannot schedule new futures after shutdown")) is True
    assert _is_shutdown_error(RuntimeError("Event loop is closed")) is True


def test_is_shutdown_error_rejects_unrelated_runtime_errors() -> None:
    assert _is_shutdown_error(RuntimeError("some other failure")) is False
    assert _is_shutdown_error(ValueError("cannot schedule new futures after shutdown")) is False
    assert _is_shutdown_error(Exception("ignored")) is False


class _ShutdownStubClient:
    """Stub ``CenturyClient`` that raises the shutdown error on the first call."""

    def __init__(self, *, fail_on: str) -> None:
        self.fail_on = fail_on

    async def fetch_player(self, fid: int):
        if self.fail_on == "fetch_player":
            raise RuntimeError("cannot schedule new futures after shutdown")
        return type("P", (), {"nickname": "x", "stove_level": 1, "fid": fid, "kid": 1, "stove_lv_content": 0, "avatar_image": ""})()

    async def fetch_captcha(self, fid: int):
        if self.fail_on == "fetch_captcha":
            raise RuntimeError("cannot schedule new futures after shutdown")
        return type("C", (), {"img_b64": "data:image/png;base64,xxx"})()

    async def redeem(self, fid: int, code: str, captcha_text: str):
        if self.fail_on == "redeem":
            raise RuntimeError("cannot schedule new futures after shutdown")
        raise NotImplementedError


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", ["fetch_player", "fetch_captcha", "redeem"])
async def test_redeem_one_propagates_shutdown_instead_of_marking_failed(
    fail_on: str, monkeypatch, tmp_path
) -> None:
    """Each network step must re-raise the shutdown error, not return ``FAILED``."""
    if fail_on == "redeem":
        # ``redeem`` is reached only after captcha solve; stub the solver to a constant.
        monkeypatch.setattr("modules.gift_codes.redeemer.solve_captcha", lambda _img: "1234")

    redeemer = GiftCodeRedeemer.__new__(GiftCodeRedeemer)
    redeemer._codes_path = tmp_path / "codes.yaml"  # not used in this path
    redeemer._devices_path = tmp_path / "devices.yaml"  # not used in this path
    redeemer._client = _ShutdownStubClient(fail_on=fail_on)

    with pytest.raises(RuntimeError, match="cannot schedule new futures"):
        await redeemer._redeem_one(765502864, "TESTCODE")
