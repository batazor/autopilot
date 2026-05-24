"""Per-test Ed25519 keypair so tests don't depend on (or pollute) the dev's secrets."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def keypair_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    private = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "license.key"
    pub_path = tmp_path / "license.pub.pem"
    priv_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("WOS_LICENSE_PRIVATE_KEY", str(priv_path))
    monkeypatch.setenv("WOS_LICENSE_PUBLIC_KEY", str(pub_path))
    return priv_path, pub_path
