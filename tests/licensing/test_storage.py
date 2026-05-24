from __future__ import annotations

import json

import pytest

from licensing.issue import issue_license
from licensing.models import LicenseError
from licensing.storage import (
    build_envelope,
    extract_token,
    license_path,
    load_token_from_file,
    save_license_file,
)


def test_envelope_round_trip(keypair_paths: object, tmp_path) -> None:
    token, payload = issue_license(
        sub="alice@example.com",
        machine_id="ABCD-EFGH-IJKL-MNOP",
        days=30,
        tier="pro",
        features=["heroes", "mail"],
    )
    envelope = build_envelope(token, payload)
    assert envelope["format"] == "wos-license-v1"
    assert envelope["issued_to"] == "alice@example.com"
    assert envelope["tier"] == "pro"
    assert envelope["features"] == ["heroes", "mail"]
    assert envelope["machine_id"] == "ABCD-EFGH-IJKL-MNOP"
    assert envelope["token"] == token

    out_path = tmp_path / "wos-license.json"
    save_license_file(envelope, out_path)
    assert out_path.is_file()
    loaded = load_token_from_file(out_path)
    assert loaded == token


def test_extract_token_accepts_bare_jwt(keypair_paths: object) -> None:
    token, _ = issue_license(sub="x@y", machine_id="X")
    assert extract_token(token) == token
    assert extract_token(token + "\n") == token


def test_extract_token_accepts_envelope(keypair_paths: object) -> None:
    token, payload = issue_license(sub="x@y", machine_id="X")
    envelope = build_envelope(token, payload)
    body = json.dumps(envelope).encode("utf-8")
    assert extract_token(body) == token


def test_extract_token_rejects_garbage() -> None:
    with pytest.raises(LicenseError) as exc_info:
        extract_token(b"this is not a license")
    assert exc_info.value.code == "bad_file"


def test_extract_token_rejects_envelope_without_token() -> None:
    body = json.dumps({"format": "wos-license-v1", "issued_to": "x"}).encode()
    with pytest.raises(LicenseError) as exc_info:
        extract_token(body)
    assert exc_info.value.code == "bad_file"


def test_extract_token_rejects_empty() -> None:
    with pytest.raises(LicenseError) as exc_info:
        extract_token(b"   \n")
    assert exc_info.value.code == "bad_file"


def test_load_token_from_file_missing(tmp_path) -> None:
    with pytest.raises(LicenseError) as exc_info:
        load_token_from_file(tmp_path / "nope.json")
    assert exc_info.value.code == "missing"


def test_license_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    custom = tmp_path / "elsewhere.json"
    monkeypatch.setenv("WOS_LICENSE_FILE", str(custom))
    assert license_path() == custom


def test_save_creates_parent_directory(keypair_paths: object, tmp_path) -> None:
    token, payload = issue_license(sub="x@y", machine_id="X")
    envelope = build_envelope(token, payload)
    deep = tmp_path / "a" / "b" / "c" / "wos-license.json"
    save_license_file(envelope, deep)
    assert deep.is_file()
