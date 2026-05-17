from __future__ import annotations

import asyncio
import concurrent.futures
import functools
from collections.abc import Callable
from typing import Any


class InstanceWorkerBlockingMixin:
    _stopping: bool
    _blocking_executor_live: bool
    _blocking_pool: concurrent.futures.ThreadPoolExecutor

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        if self._stopping or not self._blocking_executor_live:
            raise asyncio.CancelledError()
        loop = asyncio.get_running_loop()
        if kwargs:
            target: Callable[..., Any] = functools.partial(fn, *args, **kwargs)
        elif args:
            target = functools.partial(fn, *args)
        else:
            target = fn
        try:
            return await loop.run_in_executor(self._blocking_pool, target)
        except RuntimeError as exc:
            if self._stopping or "shutdown" in str(exc).lower():
                raise asyncio.CancelledError() from exc
            raise

