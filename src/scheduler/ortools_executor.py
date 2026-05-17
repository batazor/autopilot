"""Dedicated thread pool for OR-Tools CP-SAT (single worker — one solve at a time)."""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_pool: ThreadPoolExecutor | None = None
_lock = threading.RLock()


def get_ortools_executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wos-ortools")
        return _pool


def run_in_ortools_executor(loop: Any, func: Callable[..., Any], *args: Any) -> Any:
    """Submit OR-Tools work without racing executor shutdown.

    ``loop.run_in_executor(get_ortools_executor(), ...)`` has a tiny race: another
    thread can shut down the singleton after it is returned but before asyncio calls
    ``executor.submit``. Holding the same lock through ``run_in_executor`` closes
    that gap; the returned future is awaited by the caller outside the lock.
    """
    with _lock:
        executor = get_ortools_executor()
        return loop.run_in_executor(executor, func, *args)


def shutdown_ortools_executor(*, wait: bool = False, cancel_futures: bool = True) -> None:
    """Release the OR-Tools thread pool. Safe to call multiple times.

    Not registered on ``atexit``: embedded asyncio runs in a daemon thread; process exit
    runs exit handlers while that thread may still schedule work — avoid shutdown races.
    """
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=wait, cancel_futures=cancel_futures)
            _pool = None
