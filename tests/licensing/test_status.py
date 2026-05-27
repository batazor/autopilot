from __future__ import annotations

import pytest

from licensing.fingerprint import generate_fingerprint
from licensing.issue import issue_license
from licensing.models import LicenseError
from licensing.status import license_status, load_license
from licensing.storage import build_envelope, save_license_file


def test_status_missing_when_nothing_configured(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("WOS_LICENSE", raising=False)
    monkeypatch.setenv("WOS_LICENSE_FILE", str(tmp_path / "absent.json"))
    status = license_status()
    assert status.active is False
    assert status.state == "missing"
    assert status.machine_id  # fingerprint always populated


def test_status_active_via_env(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_fp = generate_fingerprint()
    token, _ = issue_license(
        sub="alice@example.com",
        machine_id=host_fp,
        tier="pro",
        features=["heroes"],
    )
    monkeypatch.setenv("WOS_LICENSE", token)
    status = license_status()
    assert status.active is True
    assert status.sub == "alice@example.com"


def test_status_active_via_file(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("WOS_LICENSE", raising=False)
    host_fp = generate_fingerprint()
    token, payload = issue_license(
        sub="bob@example.com", machine_id=host_fp, features=["mail"],
    )
    license_file = tmp_path / "licence.json"
    save_license_file(build_envelope(token, payload), license_file)
    monkeypatch.setenv("WOS_LICENSE_FILE", str(license_file))
    status = license_status()
    assert status.active is True
    assert status.sub == "bob@example.com"
    assert status.features == ["mail"]


def test_env_takes_precedence_over_file(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    host_fp = generate_fingerprint()
    env_token, _ = issue_license(sub="env-user@example.com", machine_id=host_fp)
    file_token, file_payload = issue_license(
        sub="file-user@example.com", machine_id=host_fp,
    )
    license_file = tmp_path / "licence.json"
    save_license_file(build_envelope(file_token, file_payload), license_file)
    monkeypatch.setenv("WOS_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("WOS_LICENSE", env_token)

    status = license_status()
    assert status.sub == "env-user@example.com"


def test_status_machine_mismatch(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _ = issue_license(
        sub="alice@example.com", machine_id="WRONG-FING-ERPR-INTX",
    )
    monkeypatch.setenv("WOS_LICENSE", token)
    status = license_status()
    assert status.active is False
    assert status.state == "machine_mismatch"


def test_load_license_raises_when_nothing_configured(
    keypair_paths: object, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("WOS_LICENSE", raising=False)
    monkeypatch.setenv("WOS_LICENSE_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(LicenseError) as exc_info:
        load_license()
    assert exc_info.value.code == "missing"
    # Error message should mention both lookup sources so the user knows what to do.
    assert "WOS_LICENSE" in exc_info.value.reason
    assert "absent.json" in exc_info.value.reason
