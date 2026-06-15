"""External gift-code account caps flow through issue → verify → claims.

R3 allows 5 external accounts, R4 allows 50; the cap rides the JWT as
``max_external_accounts`` and verify falls back to the tier default for
tokens minted before the claim existed.
"""
from __future__ import annotations

import pytest

from licensing.issue import issue_license
from licensing.models import LicenseError
from licensing.verify import _claims_from_payload, verify_license


def test_issue_resolves_cap_from_tier(keypair_paths: object) -> None:
    _, payload = issue_license(sub="a@b.c", machine_id="MID", tier="r3")
    assert payload["max_external_accounts"] == 5
    _, payload = issue_license(sub="a@b.c", machine_id="MID", tier="r4")
    assert payload["max_external_accounts"] == 50


def test_issue_explicit_cap_is_clamped(keypair_paths: object) -> None:
    _, payload = issue_license(
        sub="a@b.c", machine_id="MID", tier="r3", max_external_accounts=9999
    )
    assert payload["max_external_accounts"] == 1000
    _, payload = issue_license(
        sub="a@b.c", machine_id="MID", tier="r4", max_external_accounts=2
    )
    assert payload["max_external_accounts"] == 2


def test_verify_round_trips_cap(keypair_paths: object) -> None:
    token, _ = issue_license(sub="a@b.c", machine_id="MID", tier="r4")
    claims = verify_license(token, expected_machine_id="MID")
    assert claims.max_external_accounts == 50


def test_verify_falls_back_to_tier_for_legacy_tokens() -> None:
    """A payload without the cap claim resolves from a valid tier."""
    legacy = {"sub": "a@b.c", "machine_id": "MID", "tier": "r3"}
    assert _claims_from_payload(legacy).max_external_accounts == 5


def test_verify_rejects_legacy_tier_names() -> None:
    for tier in ("free", "trial", "pro"):
        with pytest.raises(LicenseError) as exc_info:
            _claims_from_payload({"sub": "a@b.c", "machine_id": "MID", "tier": tier})
        assert exc_info.value.code == "unsupported_tier"
