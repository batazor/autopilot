#!/usr/bin/env python
"""One-shot migration: decrypt SQLCipher databases back to plaintext SQLite.

The project no longer encrypts its databases. Installs that previously ran the
encrypted build have SQLCipher ``.db`` files that the plain ``sqlite3`` driver
cannot open. Run this once (with the worker stopped) to convert them.

``sqlcipher3`` is no longer a project dependency, so pull it in just for the
migration:

    uv run --with sqlcipher3-wheels python scripts/decrypt_databases.py

It is idempotent — files that are already plaintext, empty, or missing are
skipped. Each converted file keeps a ``<name>.encrypted.bak`` copy of the
original, and the now-stale encrypted ``-wal`` / ``-shm`` sidecars are removed
so the plaintext driver doesn't misread them.

This script is self-contained (it carries the old key) so it keeps working
after the SQLCipher wiring is removed from the app.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

# The application-wide key + cipher tuning the old encrypted build used. The
# key was a SQLCipher *passphrase* (so kdf_iter applied), not a raw key.
APP_SYSTEM_KEY = bytes.fromhex(
    "f49553fb3187025e7b75784f9f181c1cd984fbf284d9bf3401d079b21615899a"
)
CIPHER_PAGE_SIZE = 4096
KDF_ITER = 64000
_PLAINTEXT_HEADER = b"SQLite format 3\x00"

# Every database the app owns. Paths are relative to the repo root.
DEFAULT_DBS = [
    "db/state/state.db",
    "src/modules/notify/data/notify_monitor.db",
    "games/wos/events/dreamscape_memory/scenes.db",
]


def _is_encrypted(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as fh:
        return fh.read(len(_PLAINTEXT_HEADER)) != _PLAINTEXT_HEADER


def _quote_passphrase(key: bytes) -> str:
    return "'" + key.decode("latin-1").replace("'", "''") + "'"


def decrypt_file(path: Path) -> bool:
    """Decrypt ``path`` in place. Returns True if it converted, False if skipped."""
    if not path.exists() or path.stat().st_size == 0 or not _is_encrypted(path):
        return False

    tmp = path.with_name(f"{path.name}.plain.tmp")
    tmp.unlink(missing_ok=True)

    conn = sqlcipher.connect(str(path))
    try:
        cur = conn.cursor()
        # Key the encrypted source first, then export its full contents into a
        # fresh database attached with an empty KEY (== plaintext).
        cur.execute(f"PRAGMA key = {_quote_passphrase(APP_SYSTEM_KEY)}")
        cur.execute(f"PRAGMA cipher_page_size = {CIPHER_PAGE_SIZE}")
        cur.execute(f"PRAGMA kdf_iter = {KDF_ITER}")
        cur.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(tmp),))
        cur.execute("SELECT sqlcipher_export('plaintext')")
        cur.execute("DETACH DATABASE plaintext")
        cur.close()
    finally:
        conn.close()

    shutil.copy2(path, path.with_name(f"{path.name}.encrypted.bak"))
    tmp.replace(path)
    # The old encrypted WAL/SHM sidecars are meaningless (and dangerous) next to
    # the new plaintext file — drop them so sqlite3 starts a fresh WAL.
    path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
    path.with_name(f"{path.name}-shm").unlink(missing_ok=True)
    return True


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in (argv or DEFAULT_DBS)]
    rc = 0
    for path in paths:
        if not path.exists():
            print(f"skip (missing):           {path}")
            continue
        try:
            if decrypt_file(path):
                print(f"decrypted:                {path}  (backup: {path.name}.encrypted.bak)")
            else:
                print(f"skip (already plaintext): {path}")
        except sqlcipher.Error as exc:
            print(f"FAILED: {path}: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
