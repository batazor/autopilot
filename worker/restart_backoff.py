"""Restart-delay policy shared by both supervisor flavors.

Both ``worker.supervisor`` (multiprocess) and ``worker.async_supervisor``
(single-loop) previously restarted crashed children with a flat
``restart_wait_seconds`` delay forever. Under a fatal config or environment
error that thrashes logs and resources without ever stabilizing.

This module centralizes the policy:

* Exponential backoff capped at ``cap_seconds`` (default 5 min).
* Symmetric jitter so multiple supervisors don't restart in lockstep.
* A stability window: if the child ran without crashing for longer than
  ``stability_seconds``, the next failure is treated as the first failure
  again (counter reset). That prevents permanent slow backoff after one
  unrelated transient crash days ago.
"""

from __future__ import annotations

import random

# Module-level so tests can monkey-patch with a deterministic RNG.
_rng = random.Random()


def compute_restart_delay(
    attempt: int,
    *,
    base_seconds: float,
    cap_seconds: float = 300.0,
    jitter: float = 0.25,
) -> float:
    """Return the delay (in seconds) before the *next* restart attempt.

    ``attempt`` counts from 1 — the first restart uses ``base_seconds`` plus
    jitter. ``2 ** (attempt - 1)`` doubles each retry until capped.
    """
    if attempt < 1:
        attempt = 1
    raw = base_seconds * (2 ** (attempt - 1))
    capped = min(raw, cap_seconds)
    if jitter <= 0:
        return capped
    span = capped * jitter
    return max(0.0, capped + _rng.uniform(-span, span))
