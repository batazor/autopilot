"""Dedicated thread pool for OR-Tools CP-SAT (single worker — one solve at a time)."""

from __future__ import annotations

import atexit
import threading
from concurrent.futures import ThreadPoolExecutor

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def get_ortools_executor() -> ThreadPoolExecutor:
    global _pool  # noqa: PLW0603
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wos-ortools")
        return _pool


def _shutdown_ortools_executor() -> None:
    global _pool  # noqa: PLW0603
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


atexit.register(_shutdown_ortools_executor)
