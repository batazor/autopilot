"""Schema + seed migrations for the notify_monitor SQLite database.

There is no Alembic here — migrations are plain in-code steps gated by SQLite's
``PRAGMA user_version``, matching the rest of this repo (see ``src/config/*_db.py``).
``run_migrations()`` is idempotent and safe to call on every boot.

Two kinds of step:

* **Schema migrations** (``_SCHEMA_MIGRATIONS``) — ordered, run once. Tracked by
  ``user_version``; each bumps it by one. Use these for ``CREATE TABLE`` /
  ``ALTER TABLE`` changes.
* **Seed sync** (``_sync_seed_data``) — idempotent data backfill that runs *every*
  boot so newly-added default settings and **seed patterns** reach existing
  databases. Operator edits/deletions of *existing* rows are preserved; only
  genuinely-missing ``(game, event_type)`` pairs are inserted. This is why a new
  seed pattern in ``config.py`` lands on every DB after a restart without needing
  a fresh numbered migration each time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import Session, SQLModel, select

from . import config
from .db import DEFAULT_SETTINGS, Pattern, Setting
from .logging_setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

log = get_logger("migrations")


# --- schema migrations (run once, tracked by PRAGMA user_version) ----------

def _v1_create_tables(engine: Engine) -> None:
    """Initial schema: create every SQLModel table (no-op if already present)."""
    SQLModel.metadata.create_all(engine)


# Append new entries here for future schema changes — never reorder/remove.
_SCHEMA_MIGRATIONS: list[Callable[[Engine], None]] = [
    _v1_create_tables,
]


def _user_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def _set_user_version(engine: Engine, version: int) -> None:
    # PRAGMA does not accept bound parameters; `version` is a trusted int index.
    with engine.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version = {version}")


# --- seed sync (idempotent, runs every boot) -------------------------------

def _sync_seed_data(engine: Engine) -> None:
    """Insert any missing default settings and seed patterns. Idempotent."""
    added = 0
    with Session(engine) as s:
        for key, val in DEFAULT_SETTINGS.items():
            if s.get(Setting, key) is None:
                s.add(Setting(key=key, value=val))
        existing = {(p.game, p.event_type) for p in s.exec(select(Pattern)).all()}
        for game in config.GAMES.values():
            for event_type, regex, desc in game.seed_patterns:
                if (game.id, event_type) in existing:
                    continue
                s.add(Pattern(
                    game=game.id, pattern_regex=regex, event_type=event_type,
                    description=desc, active=True,
                ))
                added += 1
        s.commit()
    if added:
        log.info("seeded %d missing default pattern(s) for games: %s", added, ", ".join(config.GAMES))


# --- entry point -----------------------------------------------------------

def run_migrations(engine: Engine) -> None:
    """Apply pending schema migrations, then sync seed data. Idempotent."""
    current = _user_version(engine)
    target = len(_SCHEMA_MIGRATIONS)
    for version, migrate in enumerate(_SCHEMA_MIGRATIONS, start=1):
        if version <= current:
            continue
        log.info("applying schema migration v%d (%s)", version, migrate.__name__)
        migrate(engine)
        _set_user_version(engine, version)
    if current < target:
        log.info("schema at v%d", target)
    _sync_seed_data(engine)
