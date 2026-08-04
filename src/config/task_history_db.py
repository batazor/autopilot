"""Durable SQLite mirror of per-instance task execution history.

The authoritative live feed stays in Redis (``wos:queue:history:<instance>``,
capped at 50 rows / 7 days) — it powers the dashboard's realtime table. This
module is the durable shadow: the worker mirrors every history row here so
execution history survives Redis flushes and long downtime (after weeks off,
``botctl history`` used to come back empty). Reads are a fallback/top-up for
``dashboard.redis_client.fetch_queue_history_rows``, never the primary path.

Rows are stored as the exact JSON dict the worker pushes to Redis (opaque
``row_json`` blob, same pattern as ``gamers.state_json``) plus a few extracted
columns for filtering and retention. Readers re-parse the blob through the one
canonical parser (``_parse_history_row``) so the two sources cannot drift.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import Index, delete
from sqlmodel import Field, Session, SQLModel, select

from config import orm
from config.state_sqlite import state_db_path

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Age-based retention. The mirror exists for "what did the bot do last month",
# not forever-archival; DSL step traces inside ``row_json`` make rows chunky.
RETENTION_SECONDS = 60 * 60 * 24 * 30  # 30 days
# Purge at most this often per process — retention precision is irrelevant.
_PURGE_MIN_INTERVAL_S = 3600.0

_write_lock = threading.RLock()
_next_purge_ts = 0.0


class TaskHistoryRow(SQLModel, table=True):
    __tablename__ = "task_history"

    id: int | None = Field(default=None, primary_key=True)
    instance_id: str = ""
    task_type: str = ""
    player_id: str = ""
    success: bool = False
    finished_at: float = 0.0
    row_json: str = "{}"

    __table_args__ = (
        Index("ix_task_history_instance_finished", "instance_id", "finished_at"),
    )


def _ensure_schema(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine, tables=[TaskHistoryRow.__table__])


def _engine() -> Engine:
    engine = orm.get_engine(state_db_path())
    orm.ensure_once(engine, "task_history", _ensure_schema)
    return engine


def record_task_history(row: Mapping[str, Any]) -> None:
    """Mirror one Redis history row (the worker's dict, pre-serialization).

    Never raises — history mirroring must not fail a task run.
    """
    try:
        try:
            finished_at = float(row.get("finished_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            finished_at = 0.0
        db_row = TaskHistoryRow(
            instance_id=str(row.get("instance_id", "") or ""),
            task_type=str(row.get("task_type", "") or ""),
            player_id=str(row.get("player_id", "") or ""),
            success=bool(row.get("success", False)),
            finished_at=finished_at,
            row_json=json.dumps(row, ensure_ascii=False, default=str),
        )
        with _write_lock, Session(_engine()) as s:
            s.add(db_row)
            s.commit()
        _maybe_purge()
    except Exception:
        logger.debug("task history SQLite mirror write failed", exc_info=True)


def _maybe_purge(*, now: float | None = None) -> None:
    """Drop rows past retention, at most once per ``_PURGE_MIN_INTERVAL_S``."""
    global _next_purge_ts
    ts = time.time() if now is None else now
    with _write_lock:
        if ts < _next_purge_ts:
            return
        _next_purge_ts = ts + _PURGE_MIN_INTERVAL_S
        with Session(_engine()) as s:
            s.exec(  # type: ignore[call-overload]
                delete(TaskHistoryRow).where(
                    TaskHistoryRow.finished_at < ts - RETENTION_SECONDS
                )
            )
            s.commit()


def fetch_task_history_dicts(instance_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first history dicts for one instance, in the Redis row shape."""
    if limit <= 0:
        return []
    try:
        with Session(_engine()) as s:
            rows = s.exec(
                select(TaskHistoryRow)
                .where(TaskHistoryRow.instance_id == str(instance_id or "").strip())
                .order_by(TaskHistoryRow.finished_at.desc(), TaskHistoryRow.id.desc())  # type: ignore[attr-defined]
                .limit(int(limit))
            ).all()
    except Exception:
        logger.debug("task history SQLite read failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            data = json.loads(r.row_json)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out
