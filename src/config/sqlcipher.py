"""SQLCipher wiring for every SQLite database this app owns.

All persistence goes through SQLAlchemy/SQLModel engines; this module is the one
place that teaches those engines to speak SQLCipher. Engine factories
(:func:`config.orm.get_engine`, notify's ``_make_engine``) build their engine
against :data:`DBAPI_MODULE` and call :func:`apply_key_pragmas` first in their
``connect`` event hook — nothing else in the codebase opens a raw connection.

One application-wide key, :data:`APP_SYSTEM_KEY`, protects every file the same
way: no per-user key derivation, no machine fingerprinting, so any instance of
the compiled binary can open any database it wrote.

Threat model & key handling
---------------------------
The key is **not** read from the environment, a config file, or a keychain at
runtime — all of those leave the secret sitting in plaintext on disk next to the
data it protects. Instead it lives as a module constant baked into the
**Nuitka-compiled** binary. After compilation the bytes only exist inside the
machine-code executable, with no source-level ``APP_SYSTEM_KEY = ...`` line for a
casual reader to grep. This raises the bar against opportunistic access to the
``.db`` files; it is not protection against an attacker who can disassemble the
shipped binary. Treat it as encryption-at-rest, not DRM.

The key is passed to SQLCipher as a passphrase (not a raw ``x'…'`` key), so the
configured ``kdf_iter`` PBKDF2 stretch is applied.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from sqlcipher3 import dbapi2 as sqlcipher

if TYPE_CHECKING:
    import os

#: DBAPI module to hand SQLAlchemy via ``create_engine(..., module=DBAPI_MODULE)``.
#: ``sqlcipher3.dbapi2`` is API-compatible with the stdlib ``sqlite3`` driver
#: SQLAlchemy's pysqlite dialect expects, so the dialect works unchanged.
DBAPI_MODULE = sqlcipher

# ---------------------------------------------------------------------------
# Build-time secret (32 random bytes, generated with `secrets.token_bytes(32)`).
#
# Lives in source because the repo is private and the value is baked into the
# Nuitka-compiled binary. It is `bytes`, used as a SQLCipher passphrase. If this
# ever needs rotating, change it here AND migrate existing databases with
# `PRAGMA rekey` — otherwise files written under the old key become unreadable.
# ---------------------------------------------------------------------------
APP_SYSTEM_KEY = bytes.fromhex(
    "f49553fb3187025e7b75784f9f181c1cd984fbf284d9bf3401d079b21615899a"
)

# SQLCipher tuning. These MUST match across writers and readers of a file:
# changing either value makes existing databases unreadable until migrated with
# `PRAGMA rekey` / `cipher_migrate`.
CIPHER_PAGE_SIZE = 4096
KDF_ITER = 64000

# Header that marks a *plaintext* SQLite file; an encrypted file starts with
# ciphertext instead. Used to make migration idempotent.
_PLAINTEXT_HEADER = b"SQLite format 3\x00"


class DatabaseAccessError(RuntimeError):
    """Raised when an encrypted database cannot be opened or decrypted.

    Wraps the underlying ``sqlcipher3`` error so callers get one clear failure
    type whether the cause is a missing file, a wrong key, a non-SQLCipher
    file, or a corrupt/locked database.
    """


def _quote_passphrase(key: bytes) -> str:
    """Render ``key`` as a single-quoted SQL string literal for ``PRAGMA key``.

    PRAGMA statements do not accept bound parameters, so the passphrase has to
    be inlined. The key is arbitrary bytes; ``latin-1`` maps every byte (0-255)
    to exactly one code point and back without loss, and embedded single quotes
    are escaped by doubling per SQL string-literal rules.
    """
    text = key.decode("latin-1").replace("'", "''")
    return f"'{text}'"


def apply_key_pragmas(dbapi_conn: object, schema: str = "main") -> None:
    """Unlock ``dbapi_conn`` for the given attached ``schema``.

    Must be the **first** thing run on a fresh connection, before any other
    PRAGMA or query — SQLCipher derives the page layout from these, and once a
    page is read with the wrong settings the connection is poisoned. Engine
    ``connect`` hooks call this before their WAL/foreign-key pragmas.

    The default ``schema='main'`` keys the connection's own database; pass an
    attached schema name (see :func:`encrypt_file`) to key an ``ATTACH``-ed one.
    """
    prefix = "" if schema == "main" else f"{schema}."
    cur = dbapi_conn.cursor()
    try:
        # Order matters: key first, then cipher params, all before any read.
        cur.execute(f"PRAGMA {prefix}key = {_quote_passphrase(APP_SYSTEM_KEY)}")
        cur.execute(f"PRAGMA {prefix}cipher_page_size = {CIPHER_PAGE_SIZE}")
        cur.execute(f"PRAGMA {prefix}kdf_iter = {KDF_ITER}")
    finally:
        cur.close()


def connect(db_path: str | os.PathLike[str]) -> sqlcipher.Connection:
    """Open ``db_path`` as a SQLCipher database and return a keyed connection.

    A thin standalone helper (migration, one-off inspection). Application code
    should go through a SQLAlchemy engine instead — see :data:`DBAPI_MODULE`.
    Forces a decryption attempt so a wrong key or unreadable file fails here.

    Raises:
        DatabaseAccessError: the file is missing, locked, corrupt, not a
            SQLCipher database, or the key does not match.
    """
    path = str(db_path)
    try:
        conn = sqlcipher.connect(path)
        apply_key_pragmas(conn)
        # The pragmas are lazy — nothing is decrypted until a page is read. This
        # SELECT touches the schema header, surfacing a wrong key or damaged file
        # now as a clear error rather than on some later query.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.Error as exc:
        msg = (
            f"database {path!r} is inaccessible (wrong key, corrupt, "
            f"locked, or not a SQLCipher file): {exc}"
        )
        raise DatabaseAccessError(msg) from exc
    return conn


def is_encrypted(db_path: str | os.PathLike[str]) -> bool:
    """Return True if ``db_path`` exists and is not a plaintext SQLite file.

    A SQLCipher file begins with ciphertext, never the plaintext magic header,
    so the header is a reliable, cheap discriminator without touching the key.
    """
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as fh:
        return fh.read(len(_PLAINTEXT_HEADER)) != _PLAINTEXT_HEADER


def encrypt_file(db_path: str | os.PathLike[str], *, backup: bool = True) -> bool:
    """Encrypt a plaintext SQLite file in place with :data:`APP_SYSTEM_KEY`.

    Idempotent: returns False (no-op) for a missing, empty, or already-encrypted
    file. Otherwise it ``sqlcipher_export``-s the plaintext database into a fresh
    encrypted sidecar, keeps a ``<path>.plaintext.bak`` copy (when ``backup``),
    then atomically replaces the original. Returns True when it encrypted.

    Run this once per file before switching the engines over — afterwards the
    keyed engines can read it, but a plaintext reader no longer can.

    Raises:
        DatabaseAccessError: the export failed (file locked, corrupt, etc.).
    """
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0 or is_encrypted(path):
        return False

    tmp = path.with_name(f"{path.name}.sqlcipher.tmp")
    tmp.unlink(missing_ok=True)

    try:
        # Open the plaintext file with NO key on `main`, attach the target with
        # the key + cipher params, then copy schema+data across encrypted.
        plain = sqlcipher.connect(str(path))
        try:
            cur = plain.cursor()
            cur.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(tmp), _passphrase_text()))
            cur.execute(f"PRAGMA encrypted.cipher_page_size = {CIPHER_PAGE_SIZE}")
            cur.execute(f"PRAGMA encrypted.kdf_iter = {KDF_ITER}")
            cur.execute("SELECT sqlcipher_export('encrypted')")
            cur.execute("DETACH DATABASE encrypted")
            cur.close()
        finally:
            plain.close()
    except sqlcipher.Error as exc:
        tmp.unlink(missing_ok=True)
        msg = f"failed to encrypt {str(path)!r}: {exc}"
        raise DatabaseAccessError(msg) from exc

    if backup:
        shutil.copy2(path, path.with_name(f"{path.name}.plaintext.bak"))
    tmp.replace(path)
    return True


def _passphrase_text() -> str:
    """Bare passphrase (no surrounding quotes) for parameter-bound ``KEY``."""
    return APP_SYSTEM_KEY.decode("latin-1")
