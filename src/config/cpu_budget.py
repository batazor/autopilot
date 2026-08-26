"""Host CPU budgeting across worker processes.

The fleet runs one OS process per device, and each of those independently runs
CPU-bound OpenCV work (``cv2.matchTemplate`` for landmark/overlay rules), itself
sharded across threads. Nothing bounded any of it: OpenCV defaults to "use every
core", so N workers × the landmark shards × all cores oversubscribed the machine
during detect spikes — every process fighting for the same cores, adding context
switches on top of the work.

This module hands out a per-process share instead. Two levers, and they have
different timing requirements:

* :func:`apply_process_thread_caps` calls ``cv2.setNumThreads`` — a runtime API,
  effective whenever it is called, but only on builds using pthreads/TBB. The
  macOS wheel uses Grand Central Dispatch and ignores it; see that function.
* :func:`blas_thread_env` returns the OpenMP/BLAS environment variables, which
  the native libraries read **when they are loaded**. ``worker.supervisor``
  imports ``cv2``/``numpy`` at module import time, and under the ``spawn`` start
  method that happens in the child *before* its entrypoint runs — so these must
  be placed in ``os.environ`` by the parent before spawning, where the child
  inherits them.
* :func:`landmark_worker_count` divides the landmark shards by the number of
  workers sharing the host. This is the lever that works everywhere, and the
  only one that works on macOS.
"""
from __future__ import annotations

import contextlib
import os

# Threads a single worker's OpenCV may use. The landmark path already shards
# rules across threads itself (navigation.detector), so per-call intra-op
# parallelism on top of that is mostly redundant — it multiplies rather than
# adds. Keep a little so a lone large matchTemplate isn't fully serial.
WORKER_CV2_THREADS = 2

# Upper bound on landmark shards per worker, unchanged from the original intent:
# leave headroom for the worker's snapshot capture, OCR client and Redis I/O.
MAX_LANDMARK_WORKERS = 4


def logical_cpus() -> int:
    return os.cpu_count() or 1


def landmark_worker_count(instance_count: int | None = None) -> int:
    """Landmark shards for this process, given how many workers share the host.

    With one device the old ``min(4, cpus)`` still applies. With several, the
    host is divided so the fleet's combined shard count stays within the
    machine rather than each worker assuming it owns every core.
    """
    cpus = logical_cpus()
    n = instance_count if instance_count and instance_count > 0 else 1
    return max(1, min(MAX_LANDMARK_WORKERS, cpus // n))


def blas_thread_env(threads: int = WORKER_CV2_THREADS) -> dict[str, str]:
    """OpenMP/BLAS thread caps to place in the environment before spawning.

    Covers the runtimes that ship transitively (OpenCV's OpenMP builds, numpy's
    OpenBLAS, Accelerate on macOS, MKL, numexpr). Each defaults to one thread
    per core, so N processes each spawn N×cores threads without these.
    """
    value = str(max(1, int(threads)))
    return {
        "OMP_NUM_THREADS": value,
        "OPENBLAS_NUM_THREADS": value,
        "MKL_NUM_THREADS": value,
        "NUMEXPR_NUM_THREADS": value,
        "VECLIB_MAXIMUM_THREADS": value,
    }


def export_blas_thread_env(threads: int = WORKER_CV2_THREADS) -> None:
    """Set the BLAS caps in this process's environment for children to inherit.

    Respects a cap the operator set explicitly — only fills in what is missing.
    """
    for key, value in blas_thread_env(threads).items():
        os.environ.setdefault(key, value)


def apply_process_thread_caps(threads: int = WORKER_CV2_THREADS) -> None:
    """Cap OpenCV's intra-op threads for this process.

    Safe to call after ``cv2`` is imported — unlike the environment variables,
    this takes effect immediately. Effective on builds whose parallel framework
    is pthreads or TBB (the Linux/Docker wheels).

    **Not effective on macOS**, where the wheel is built against Grand Central
    Dispatch. GCD owns its thread pool, so ``setNumThreads(n)`` for ``n > 0`` is
    silently ignored — ``getNumThreads()`` keeps reporting the core count. The
    call is left in because it *is* the right cap everywhere else, and it is a
    harmless no-op here.

    Do **not** "fix" that by passing 0. Zero is the one value GCD honours, and
    it means fully serial: measured on a 720x1280 frame, that leaves
    matchTemplate and the phash unchanged but makes ``cv2.Canny`` — which sits
    on the template-match edge-similarity path — 7.8x slower (1.8 ms → 13.8 ms).
    On macOS the levers that actually work are :func:`landmark_worker_count`
    (fewer concurrent shards per worker) and :func:`blas_thread_env`.
    """
    try:
        import cv2
    except Exception:  # pragma: no cover - cv2 always present in the worker
        return
    with contextlib.suppress(Exception):  # depends on the OpenCV build
        cv2.setNumThreads(max(1, int(threads)))
