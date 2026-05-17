from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from scheduler.ortools_executor import run_in_ortools_executor, shutdown_ortools_executor

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future, ThreadPoolExecutor


class _RaceLoop:
    def __init__(self) -> None:
        self.shutdown_thread: threading.Thread | None = None

    def run_in_executor(
        self,
        executor: ThreadPoolExecutor,
        func: Callable[..., Any],
        *args: Any,
    ) -> Future[Any]:
        def shutdown() -> None:
            shutdown_ortools_executor(wait=False, cancel_futures=False)

        thread = threading.Thread(target=shutdown)
        self.shutdown_thread = thread
        thread.start()
        time.sleep(0.02)
        return executor.submit(func, *args)


def test_run_in_ortools_executor_submit_does_not_race_shutdown() -> None:
    loop = _RaceLoop()
    try:
        future = run_in_ortools_executor(loop, lambda: "ok")
        if loop.shutdown_thread is not None:
            loop.shutdown_thread.join(timeout=1)
        assert future.result(timeout=1) == "ok"
    finally:
        shutdown_ortools_executor(wait=False, cancel_futures=True)
