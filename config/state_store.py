"""Per-gamer state manager: loads db/state.yaml, flattens to dot-notation, syncs Redis."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from config.state_schema import GamerState, StateDB

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).parent.parent / "db" / "state.yaml"
# sentinel for getattr default — distinguishes "attr is None" from "attr missing"
_MISSING = object()

_on_save_callbacks: list[Callable[[], None]] = []
_on_save_lock = threading.Lock()


def register_on_save(callback: Callable[[], None]) -> None:
    """Register a no-arg callback invoked after every persistent state save.

    Idempotent — registering the same callable twice keeps a single entry.
    """
    with _on_save_lock:
        if callback not in _on_save_callbacks:
            _on_save_callbacks.append(callback)


def _fire_on_save_callbacks() -> None:
    with _on_save_lock:
        callbacks = list(_on_save_callbacks)
    for cb in callbacks:
        try:
            cb()
        except Exception:
            logger.debug("state_store on_save callback failed", exc_info=True)


def _flatten(obj: Any, prefix: str, out: dict[str, Any]) -> None:
    """Recursively flatten a nested dict/model to dot-notation keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif hasattr(obj, "model_dump"):
        _flatten(obj.model_dump(), prefix, out)
    else:
        out[prefix] = obj


class GamerStateStore:
    """Thread-safe state store for one gamer. Persists to db/state.yaml."""

    def __init__(self, gamer: GamerState, db: StateDB, path: Path, lock: threading.RLock) -> None:
        self._gamer = gamer
        self._db = db
        self._path = path
        self._lock = lock

    @property
    def player_id(self) -> str:
        return str(self._gamer.id)

    def to_flat_dict(self) -> dict[str, Any]:
        """Return state as dot-notation flat dict for use case expression evaluation."""
        with self._lock:
            flat: dict[str, Any] = {}
            _flatten(self._gamer.model_dump(), "", flat)
            return flat

    def get(self, key: str, default: Any = None) -> Any:
        flat = self.to_flat_dict()
        return flat.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a dot-notation key by navigating the model structure."""
        with self._lock:
            parts = key.split(".")
            self._set_nested(self._gamer, parts, value)
            self._persist()

    def _set_nested(self, obj: Any, parts: list[str], value: Any) -> None:
        if not parts:
            return
        attr = parts[0]
        if len(parts) == 1:
            if isinstance(obj, dict):
                obj[attr] = value
            elif hasattr(obj, attr):
                setattr(obj, attr, value)
            else:
                logger.warning("_set_nested: unknown attribute %r on %s", attr, type(obj).__name__)
        else:
            if isinstance(obj, dict):
                child = obj.get(attr)
            else:
                child = getattr(obj, attr, _MISSING)
                if child is _MISSING:
                    logger.warning(
                        "_set_nested: unknown attribute %r on %s — key not set",
                        attr,
                        type(obj).__name__,
                    )
                    return
            if child is not None:
                self._set_nested(child, parts[1:], value)

    def update_from_flat(self, flat: dict[str, Any]) -> None:
        """Bulk-update from a flat dot-notation dict and persist once."""
        with self._lock:
            for key, value in flat.items():
                parts = key.split(".")
                self._set_nested(self._gamer, parts, value)
            self._persist()

    def _persist(self) -> None:
        _save_state_db(self._db, self._path)

    def snapshot(self) -> GamerState:
        with self._lock:
            return self._gamer.model_copy(deep=True)


class StateStore:
    """Multi-gamer state store backed by db/state.yaml."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _STATE_PATH
        self._lock = threading.RLock()
        self._db: StateDB = _load_state_db(self._path)
        self._stores: dict[str, GamerStateStore] = {
            str(g.id): GamerStateStore(g, self._db, self._path, self._lock)
            for g in self._db.gamers
        }

    def get_or_create(self, player_id: str, nickname: str = "") -> GamerStateStore:
        with self._lock:
            if player_id not in self._stores:
                gamer = GamerState(id=int(player_id), nickname=nickname)
                self._db.gamers.append(gamer)
                store = GamerStateStore(gamer, self._db, self._path, self._lock)
                self._stores[player_id] = store
                _save_state_db(self._db, self._path)
            return self._stores[player_id]

    def get(self, player_id: str) -> GamerStateStore | None:
        with self._lock:
            return self._stores.get(player_id)

    def all_player_ids(self) -> list[str]:
        with self._lock:
            return list(self._stores.keys())

    def reload(self) -> None:
        with self._lock:
            new_db = _load_state_db(self._path)
            new_gamers = {str(g.id): g for g in new_db.gamers}
            # Update existing stores in-place so callers that cached a GamerStateStore
            # reference don't silently keep writing to the pre-reload _db.
            for pid, store in self._stores.items():
                if pid in new_gamers:
                    store._gamer = new_gamers[pid]
                    store._db = new_db
            for pid, gamer in new_gamers.items():
                if pid not in self._stores:
                    self._stores[pid] = GamerStateStore(gamer, new_db, self._path, self._lock)
            self._db = new_db


def _load_state_db(path: Path) -> StateDB:
    if not path.exists():
        return StateDB()
    raw = yaml.safe_load(path.read_text()) or {}
    return StateDB.model_validate(raw)


def _save_state_db(db: StateDB, path: Path) -> None:
    try:
        data = db.model_dump(mode="json")
        content = yaml.dump(data, allow_unicode=True, sort_keys=False)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name
        os.replace(tmp, path)
    except Exception:
        logger.exception("Failed to persist state to %s", path)
        return
    _fire_on_save_callbacks()


_global_store: StateStore | None = None
_global_store_lock = threading.Lock()


def get_state_store() -> StateStore:
    global _global_store  # noqa: PLW0603
    if _global_store is None:
        with _global_store_lock:
            if _global_store is None:
                _global_store = StateStore()
    return _global_store
