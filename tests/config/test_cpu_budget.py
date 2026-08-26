"""Per-process CPU budgeting (config/cpu_budget.py)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from config import cpu_budget
from config.cpu_budget import (
    MAX_LANDMARK_WORKERS,
    blas_thread_env,
    export_blas_thread_env,
    landmark_worker_count,
)

if TYPE_CHECKING:
    import pytest


def test_shards_shrink_as_workers_share_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this exists for: every worker assuming it owns the host.

    One process per device, each sharding landmark rules across threads — with
    no divisor the fleet's combined shard count grows linearly with devices and
    oversubscribes the machine during a detect spike.
    """
    monkeypatch.setattr(cpu_budget, "logical_cpus", lambda: 8)
    solo = landmark_worker_count(1)
    shared = landmark_worker_count(4)
    assert shared < solo
    # The fleet as a whole must still fit the machine.
    assert shared * 4 <= 8


def test_single_instance_keeps_the_original_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_budget, "logical_cpus", lambda: 8)
    assert landmark_worker_count(1) == MAX_LANDMARK_WORKERS
    # Unknown instance count falls back to the same single-worker sizing, so a
    # caller that can't resolve settings is never penalised.
    assert landmark_worker_count(None) == landmark_worker_count(1)


def test_never_returns_zero_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    # More devices than cores must still yield a usable shard count, not 0
    # (which would evaluate no rules at all).
    monkeypatch.setattr(cpu_budget, "logical_cpus", lambda: 2)
    assert landmark_worker_count(16) >= 1
    monkeypatch.setattr(cpu_budget, "logical_cpus", lambda: 1)
    assert landmark_worker_count(1) >= 1


def test_blas_env_covers_every_threading_runtime_we_pull_in() -> None:
    env = blas_thread_env(2)
    # Each of these defaults to one thread per core; missing one leaves that
    # runtime uncapped and the cap silently half-works.
    assert {
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    } <= set(env)
    assert all(int(v) >= 1 for v in env.values())


def test_blas_env_never_caps_below_one() -> None:
    # A zero/negative cap would be read as "unlimited" by some runtimes.
    assert all(int(v) >= 1 for v in blas_thread_env(0).values())
    assert all(int(v) >= 1 for v in blas_thread_env(-4).values())


def test_export_respects_an_operator_set_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_NUM_THREADS", "7")
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    export_blas_thread_env(2)
    assert os.environ["OMP_NUM_THREADS"] == "7"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
