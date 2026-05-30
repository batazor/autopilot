from __future__ import annotations

import concurrent.futures
import threading

import pytest

from worker.instance_worker import InstanceWorker


@pytest.mark.asyncio
async def test_instance_worker_uses_dedicated_rolling_executor() -> None:
    worker = object.__new__(InstanceWorker)
    worker._stopping = False
    worker._rolling_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="test-rolling",
    )

    try:
        thread_name = await worker._run_rolling_blocking(
            lambda: threading.current_thread().name,
        )
    finally:
        worker._rolling_pool.shutdown(wait=True, cancel_futures=True)

    assert thread_name.startswith("test-rolling")
