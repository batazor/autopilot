from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config import sqlcipher

if TYPE_CHECKING:
    from pathlib import Path


def test_roundtrip_reads_back_written_data(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = sqlcipher.connect(db_path)
    conn.execute("CREATE TABLE x(v TEXT)")
    conn.execute("INSERT INTO x VALUES (?)", ("secret",))
    conn.commit()
    conn.close()

    conn = sqlcipher.connect(db_path)
    assert conn.execute("SELECT v FROM x").fetchone()[0] == "secret"
    conn.close()


def test_file_on_disk_is_encrypted(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = sqlcipher.connect(db_path)
    conn.execute("CREATE TABLE x(v TEXT)")
    conn.commit()
    conn.close()

    # A plaintext SQLite file begins with this header; an encrypted one must not.
    assert db_path.read_bytes()[:16] != b"SQLite format 3\x00"


def test_cipher_parameters_applied(tmp_path: Path) -> None:
    conn = sqlcipher.connect(tmp_path / "t.db")
    try:
        assert int(conn.execute("PRAGMA cipher_page_size").fetchone()[0]) == sqlcipher.CIPHER_PAGE_SIZE
        assert int(conn.execute("PRAGMA kdf_iter").fetchone()[0]) == sqlcipher.KDF_ITER
    finally:
        conn.close()


def test_wrong_key_raises_database_access_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "t.db"
    conn = sqlcipher.connect(db_path)
    conn.execute("CREATE TABLE x(v TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(sqlcipher, "APP_SYSTEM_KEY", b"a-completely-different-key-000000")
    with pytest.raises(sqlcipher.DatabaseAccessError):
        sqlcipher.connect(db_path)


def test_non_sqlcipher_file_raises_database_access_error(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.db"
    bad.write_bytes(b"not a database at all")
    with pytest.raises(sqlcipher.DatabaseAccessError):
        sqlcipher.connect(bad)


def test_is_encrypted_discriminates(tmp_path: Path) -> None:
    import sqlite3

    missing = tmp_path / "nope.db"
    assert sqlcipher.is_encrypted(missing) is False

    plain = tmp_path / "plain.db"
    conn = sqlite3.connect(plain)
    conn.execute("CREATE TABLE t(v)")
    conn.commit()
    conn.close()
    assert sqlcipher.is_encrypted(plain) is False

    enc = tmp_path / "enc.db"
    c = sqlcipher.connect(enc)
    c.execute("CREATE TABLE t(v)")
    c.commit()
    c.close()
    assert sqlcipher.is_encrypted(enc) is True


def test_encrypt_file_migrates_plaintext_in_place(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", ("payload",))
    conn.commit()
    conn.close()
    assert sqlcipher.is_encrypted(db_path) is False

    assert sqlcipher.encrypt_file(db_path) is True
    assert sqlcipher.is_encrypted(db_path) is True
    # plaintext backup is kept and still readable as a copy of the original
    assert (tmp_path / "state.db.plaintext.bak").exists()

    # data survives and is now only reachable with the key
    conn = sqlcipher.connect(db_path)
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "payload"
    conn.close()


def test_encrypt_file_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "already.db"
    c = sqlcipher.connect(db_path)
    c.execute("CREATE TABLE t(v)")
    c.commit()
    c.close()
    # already encrypted, and missing files, are no-ops
    assert sqlcipher.encrypt_file(db_path) is False
    assert sqlcipher.encrypt_file(tmp_path / "missing.db") is False


def test_encrypt_file_no_backup(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "s.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(v)")
    conn.commit()
    conn.close()

    assert sqlcipher.encrypt_file(db_path, backup=False) is True
    assert not (tmp_path / "s.db.plaintext.bak").exists()
