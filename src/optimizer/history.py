"""Append-only audit log of operator-approved upgrade commands.

Lives at ``db/optimizer_history.yaml`` so it ships next to ``state.yaml``
and is easy to diff in a review. Each entry captures the candidate, its
score breakdown, the state diff that was persisted, and the active
profile at decision time — enough to reconstruct *why* the operator
acted (or to spot scoring drift later).

Reading: :func:`load_history`. Writing: :func:`append_entry`. Trimming:
:func:`trim_history` keeps the latest ``max_entries`` records so the file
doesn't grow without bound.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

_HISTORY_PATH = repo_root() / "db" / "optimizer_history.yaml"
_DEFAULT_MAX_ENTRIES = 500

# In-process serialisation for the read-modify-write cycle. The history is also
# touched by external editors / git checkouts; a process-wide lock prevents
# concurrent append_entry calls in the same Python process from clobbering each
# other (cross-process protection would need a file lock, but the audit trail
# is single-writer in practice — only the approval UI calls append_entry).
_WRITE_LOCK = threading.Lock()


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via tmp+rename, cleaning up on failure.

    A bare ``path.write_text(...)`` can leave the file half-truncated if the
    process is interrupted mid-write — ``load_history`` then silently returns
    ``[]`` and the approval audit trail is gone. tmp+rename keeps the on-disk
    file either fully-old or fully-new.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            with suppress(OSError):
                os.unlink(tmp)


@dataclass(frozen=True)
class HistoryEntry:
    approved_at: float
    gamer_id: str
    profile: str
    candidate_id: str
    action: str
    hero_id: str | None
    score: float
    costs: list[dict[str, Any]]
    state_diff: dict[str, dict[str, Any]] = field(default_factory=dict)
    """``{key: {before, after}}`` per persisted state-flat field."""
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _path() -> Path:
    return _HISTORY_PATH


def load_history(path: Path | None = None) -> list[HistoryEntry]:
    p = path or _path()
    if not p.is_file():
        return []
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except yaml.YAMLError:
        return []
    out: list[HistoryEntry] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            HistoryEntry(
                approved_at=float(item.get("approved_at") or 0.0),
                gamer_id=str(item.get("gamer_id") or ""),
                profile=str(item.get("profile") or ""),
                candidate_id=str(item.get("candidate_id") or ""),
                action=str(item.get("action") or ""),
                hero_id=str(item.get("hero_id")) if item.get("hero_id") else None,
                score=float(item.get("score") or 0.0),
                costs=list(item.get("costs") or []),
                state_diff=dict(item.get("state_diff") or {}),
                reasons=list(item.get("reasons") or []),
                notes=list(item.get("notes") or []),
            )
        )
    return out


def append_entry(entry: HistoryEntry, path: Path | None = None, *, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
    """Read existing log, append, trim, write. Serialised + atomic on disk."""
    p = path or _path()
    with _WRITE_LOCK:
        entries = load_history(p)
        entries.append(entry)
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        raw = [
            {
                "approved_at": e.approved_at,
                "gamer_id": e.gamer_id,
                "profile": e.profile,
                "candidate_id": e.candidate_id,
                "action": e.action,
                "hero_id": e.hero_id,
                "score": e.score,
                "costs": e.costs,
                "state_diff": e.state_diff,
                "reasons": e.reasons,
                "notes": e.notes,
            }
            for e in entries
        ]
        _atomic_write(p, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


def trim_history(max_entries: int = _DEFAULT_MAX_ENTRIES, path: Path | None = None) -> int:
    """Trim the log to ``max_entries`` newest. Returns count removed."""
    p = path or _path()
    with _WRITE_LOCK:
        entries = load_history(p)
        if len(entries) <= max_entries:
            return 0
        kept = entries[-max_entries:]
        raw = [
            {
                "approved_at": e.approved_at,
                "gamer_id": e.gamer_id,
                "profile": e.profile,
                "candidate_id": e.candidate_id,
                "action": e.action,
                "hero_id": e.hero_id,
                "score": e.score,
                "costs": e.costs,
                "state_diff": e.state_diff,
                "reasons": e.reasons,
                "notes": e.notes,
            }
            for e in kept
        ]
        _atomic_write(p, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
        return len(entries) - len(kept)


def now_ts() -> float:
    return time.time()
