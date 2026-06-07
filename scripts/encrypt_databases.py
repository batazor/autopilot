#!/usr/bin/env python3
"""One-shot migration: encrypt every plaintext SQLite database in place.

Run this ONCE after deploying the SQLCipher change and BEFORE starting the
worker/API with the new code — the keyed engines cannot read a plaintext file,
and a plaintext reader cannot read an encrypted one, so the switch has to happen
while nothing is holding the databases open.

    uv run python scripts/encrypt_databases.py            # encrypt + keep .bak
    uv run python scripts/encrypt_databases.py --no-backup

Idempotent: already-encrypted, empty, or missing files are skipped, so it is
safe to re-run. Each migrated file leaves a ``<path>.plaintext.bak`` copy unless
``--no-backup`` is passed — delete those once you've confirmed the app reads the
encrypted data.

NOT covered: the diskcache template-search cache under
``.cache/wos/search_positions/`` — it is managed by the third-party ``diskcache``
library (not a SQLAlchemy engine), holds only regenerable match coordinates (no
secrets), and is rebuilt automatically if deleted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src/` importable when run as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import dreamscape_db, sqlcipher
from config.state_sqlite import state_db_path


def _target_paths() -> list[Path]:
    """Canonical DB paths, resolved through each module's own accessor.

    Importing the modules (rather than hard-coding paths) keeps this in step
    with env overrides like ``NM_DB_PATH`` and the test path hooks.
    """
    paths = [state_db_path(), dreamscape_db.db_path()]
    # notify lives under src/modules/; import lazily so a missing optional
    # package doesn't abort the whole migration.
    try:
        from modules.notify import config as notify_config

        paths.append(Path(notify_config.DB_PATH))
    except ImportError as exc:
        print(f"  (skipping notify DB: {exc})")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not keep a <path>.plaintext.bak copy of each migrated file",
    )
    args = parser.parse_args()
    backup = not args.no_backup

    encrypted = skipped = 0
    for path in _target_paths():
        if not path.exists() or path.stat().st_size == 0:
            print(f"skip (missing/empty): {path}")
            skipped += 1
            continue
        if sqlcipher.is_encrypted(path):
            print(f"skip (already encrypted): {path}")
            skipped += 1
            continue
        try:
            sqlcipher.encrypt_file(path, backup=backup)
        except sqlcipher.DatabaseAccessError as exc:
            print(f"FAILED: {path}: {exc}")
            return 1
        print(f"encrypted: {path}" + ("  (backup: .plaintext.bak)" if backup else ""))
        encrypted += 1

    print(f"\nDone. {encrypted} encrypted, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
