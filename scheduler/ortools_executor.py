"""Dedicated thread pool for OR-Tools CP-SAT (single worker — one solve at a time)."""

from __future__ import annotations

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


def shutdown_ortools_executor(*, wait: bool = False, cancel_futures: bool = True) -> None:
    """Release the OR-Tools thread pool. Safe to call multiple times.

    Not registered on ``atexit``: embedded asyncio runs in a daemon thread; process exit
    runs exit handlers while that thread may still schedule work — avoid shutdown races.
    """
    global _pool  # noqa: PLW0603
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=wait, cancel_futures=cancel_futures)
            _pool = None
