from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from licensing.issue import issue_license
from licensing.keys import load_private_key
from licensing.models import LicenseError
from licensing.verify import ALGORITHM, ISSUER, verify_license


def test_verify_happy_path(keypair_paths: object) -> None:
    token, _ = issue_license(
        sub="alice@example.com",
        machine_id="ABCD-EFGH-IJKL-MNOP",
        days=30,
        tier="r4",
    )
    claims = verify_license(token, expected_machine_id="ABCD-EFGH-IJKL-MNOP")
    assert claims.sub == "alice@example.com"
    assert claims.tier == "r4"
    assert claims.machine_id == "ABCD-EFGH-IJKL-MNOP"


def test_verify_rejects_wrong_machine(keypair_paths: object) -> None:
    token, _ = issue_license(
        sub="alice@example.com", machine_id="AAAA-BBBB-CCCC-DDDD",
    )
    with pytest.raises(LicenseError) as exc_info:
        verify_license(token, expected_machine_id="ZZZZ-YYYY-XXXX-WWWW")
    assert exc_info.value.code == "machine_mismatch"


def test_verify_accepts_wildcard_machine_id(keypair_paths: object) -> None:
    """Wildcard tokens carry machine_id='*' and bypass the host-binding check."""
    token, _ = issue_license(sub="trial@autopilot", machine_id="*", tier="r2")
    claims = verify_license(token, expected_machine_id="ANY-HOST-FINGERPRINT")
    assert claims.machine_id == "*"
    assert claims.sub == "trial@autopilot"
    assert claims.tier == "r2"


def test_verify_rejects_legacy_trial_tier(keypair_paths: object) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "trial@autopilot",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=60)).timestamp()),
            "machine_id": "*",
            "tier": "trial",
            "jti": "legacy-trial",
        },
        load_private_key(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(LicenseError) as exc_info:
        verify_license(token, expected_machine_id="ANY-HOST-FINGERPRINT")
    assert exc_info.value.code == "unsupported_tier"


def test_verify_rejects_expired(keypair_paths: object) -> None:
    past = datetime.now(UTC) - timedelta(days=60)
    token, _ = issue_license(
        sub="alice@example.com",
        machine_id="AAAA-BBBB-CCCC-DDDD",
        days=30,
        issued_at=past,
    )
    with pytest.raises(LicenseError) as exc_info:
        verify_license(token)
    assert exc_info.value.code == "expired"


def test_verify_rejects_tampered_signature(keypair_paths: object) -> None:
    token, _ = issue_license(sub="alice@example.com", machine_id="X")
    # Flip a few bytes in the signature segment (last component of the JWT).
    head, payload, sig = token.split(".")
    bad = head + "." + payload + "." + ("A" * len(sig))
    with pytest.raises(LicenseError) as exc_info:
        verify_license(bad)
    assert exc_info.value.code in {"bad_signature", "invalid"}


def test_verify_empty_token(keypair_paths: object) -> None:
    with pytest.raises(LicenseError) as exc_info:
        verify_license("")
    assert exc_info.value.code == "missing"


def test_issue_requires_sub_and_machine(keypair_paths: object) -> None:
    with pytest.raises(ValueError, match="sub"):
        issue_license(sub="", machine_id="X")
    with pytest.raises(ValueError, match="machine_id"):
        issue_license(sub="x", machine_id="")
