"""``uv run gen-license-keypair`` — one-off bootstrap.

Writes the Ed25519 private key to ``.secrets/license_signer.key`` (gitignored)
and the public key to ``src/licensing/public_key.pem`` (commit). Refuses to
overwrite existing keys unless ``--force`` is passed.
"""
from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing.keys import private_key_path, public_key_path

if TYPE_CHECKING:
    from pathlib import Path


def _write_key(path: Path, pem: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)
    path.chmod(mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen-license-keypair",
        description="Generate a fresh Ed25519 keypair for license signing.",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing keys")
    args = parser.parse_args(argv)

    pub_path = public_key_path()
    priv_path = private_key_path()

    if not args.force and (pub_path.is_file() or priv_path.is_file()):
        print("error: refusing to overwrite existing keys (use --force):", file=sys.stderr)
        print(f"  public:  {pub_path} ({'exists' if pub_path.is_file() else 'missing'})", file=sys.stderr)
        print(f"  private: {priv_path} ({'exists' if priv_path.is_file() else 'missing'})", file=sys.stderr)
        return 2

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    _write_key(priv_path, private_pem, mode=0o600)
    _write_key(pub_path, public_pem, mode=0o644)

    print("generated Ed25519 keypair:")
    print(f"  private (KEEP SECRET): {priv_path}")
    print(f"  public  (commit this): {pub_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
