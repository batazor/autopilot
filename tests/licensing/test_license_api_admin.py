"""Admin-token gate on the license issuer endpoint (``authorize_admin``)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from api.services import license_api as svc

if TYPE_CHECKING:
    from pathlib import Path


def test_no_private_key_returns_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Point the private key at a nonexistent path → issuing unavailable here.
    monkeypatch.setenv("WOS_LICENSE_PRIVATE_KEY", str(tmp_path / "absent.key"))
    monkeypatch.setenv(svc.ADMIN_TOKEN_ENV, "s3cret")
    with pytest.raises(HTTPException) as exc:
        svc.authorize_admin("s3cret")
    assert exc.value.status_code == 404


def test_private_key_present_but_no_admin_token_returns_403(
    keypair_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(svc.ADMIN_TOKEN_ENV, raising=False)
    with pytest.raises(HTTPException) as exc:
        svc.authorize_admin("anything")
    assert exc.value.status_code == 403


def test_wrong_token_returns_401(
    keypair_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(svc.ADMIN_TOKEN_ENV, "correct-token")
    with pytest.raises(HTTPException) as exc:
        svc.authorize_admin("wrong-token")
    assert exc.value.status_code == 401


def test_missing_token_returns_401(
    keypair_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(svc.ADMIN_TOKEN_ENV, "correct-token")
    with pytest.raises(HTTPException) as exc:
        svc.authorize_admin(None)
    assert exc.value.status_code == 401


def test_correct_token_authorizes(
    keypair_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(svc.ADMIN_TOKEN_ENV, "correct-token")
    # No exception == authorized.
    svc.authorize_admin("correct-token")


def test_compare_is_constant_time(
    keypair_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A token that shares a prefix but differs in length must still be rejected
    # (compare_digest handles unequal lengths without raising).
    monkeypatch.setenv(svc.ADMIN_TOKEN_ENV, "correct-token")
    with pytest.raises(HTTPException) as exc:
        svc.authorize_admin("correct-token-extra")
    assert exc.value.status_code == 401
