"""Process-wide service registry.

This module deliberately holds only data and primitive accessors — no
business logic. Streamlit's hot-reload re-imports only modules whose file
mtime changed; keeping this file thin means the registry survives reloads
of higher-level modules like :mod:`services` and ``ui.*``.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.RLock()
_services: dict[str, Any] = {}


def get(key: str) -> Any | None:
    with _lock:
        return _services.get(key)


def set_(key: str, value: Any) -> None:
    with _lock:
        _services[key] = value


def pop(key: str) -> Any | None:
    with _lock:
        return _services.pop(key, None)


def has(key: str) -> bool:
    with _lock:
        return key in _services


def clear() -> None:
    with _lock:
        _services.clear()
