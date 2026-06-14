from __future__ import annotations

import pytest

from licensing.issue import issue_license
from licensing.models import LicenseError
from licensing.storage import (
    extract_token,
    license_path,
    load_or_create_host_id,
    load_token_from_file,
    save_token_to_file,
)


def test_save_and_load_round_trip(keypair_paths: object, tmp_path) -> None:
    token, _ = issue_license(
        sub="alice@example.com",
        machine_id="ABCD-EFGH-IJKL-MNOP",
        days=30,
        tier="r4",
    )
    out_path = tmp_path / "licence.jwt"
    save_token_to_file(token, out_path)
    assert out_path.is_file()
    assert load_token_from_file(out_path) == token


def test_extract_token_accepts_bare_jwt(keypair_paths: object) -> None:
    token, _ = issue_license(sub="x@y", machine_id="X")
    assert extract_token(token) == token
    assert extract_token(token + "\n") == token
    assert extract_token(token.encode("utf-8")) == token


def test_extract_token_rejects_garbage() -> None:
    with pytest.raises(LicenseError) as exc_info:
        extract_token(b"this is not a license")
    assert exc_info.value.code == "bad_file"


def test_extract_token_rejects_json_envelope() -> None:
    """Old JSON-envelope format is no longer accepted — file must be raw JWT."""
    with pytest.raises(LicenseError) as exc_info:
        extract_token(b'{"format": "wos-license-v1", "token": "x.y.z"}')
    assert exc_info.value.code == "bad_file"


def test_extract_token_rejects_empty() -> None:
    with pytest.raises(LicenseError) as exc_info:
        extract_token(b"   \n")
    assert exc_info.value.code == "bad_file"


def test_load_token_from_file_missing(tmp_path) -> None:
    with pytest.raises(LicenseError) as exc_info:
        load_token_from_file(tmp_path / "nope.jwt")
    assert exc_info.value.code == "missing"


def test_license_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    custom = tmp_path / "elsewhere.jwt"
    monkeypatch.setenv("WOS_LICENSE_FILE", str(custom))
    assert license_path() == custom


def test_load_or_create_host_id_persists_value(tmp_path) -> None:
    path = tmp_path / "license-data" / "host-id"
    first = load_or_create_host_id(path)
    second = load_or_create_host_id(path)

    assert first
    assert second == first
    assert path.read_text(encoding="utf-8").strip() == first


def test_load_or_create_host_id_returns_empty_when_unwritable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "license-data" / "host-id"
    monkeypatch.setattr("licensing.storage.os.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))

    assert load_or_create_host_id(path) == ""


def test_save_creates_parent_directory(keypair_paths: object, tmp_path) -> None:
    token, _ = issue_license(sub="x@y", machine_id="X")
    deep = tmp_path / "a" / "b" / "c" / "licence.jwt"
    save_token_to_file(token, deep)
    assert deep.is_file()


def test_save_rejects_empty_token(tmp_path) -> None:
    with pytest.raises(LicenseError) as exc_info:
        save_token_to_file("", tmp_path / "licence.jwt")
    assert exc_info.value.code == "bad_file"
