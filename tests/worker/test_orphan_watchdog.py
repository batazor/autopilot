"""Orphan watchdog: a detached/forked worker self-terminates when its spawner dies.

This is the fix for "I terminated the process but the logs keep coming" — an
isolated ``instance_runner`` (fish-detect Play) was started with
``start_new_session`` and orphaned to launchd when its parent (the API) died,
running forever. The watchdog SIGTERMs the worker once it notices the reparent.
"""

from __future__ import annotations

from unittest.mock import patch

from worker import supervisor


def test_orphan_watchdog_skips_when_already_orphaned() -> None:
    """Started under init/launchd (PID <= 1) → no owning parent → no thread."""
    with (
        patch.object(supervisor.os, "getppid", return_value=1),
        patch.object(supervisor.threading, "Thread") as thread,
    ):
        supervisor._install_orphan_watchdog()
    thread.assert_not_called()


def test_orphan_watchdog_self_terminates_on_reparent() -> None:
    """getppid changes (parent died → reparented) → SIGTERM ourselves."""
    captured: dict[str, object] = {}

    class _CaptureThread:
        def __init__(self, *, target, daemon, name) -> None:
            captured["target"] = target

        def start(self) -> None:
            return None

    # First getppid() call seeds initial_ppid (500); the loop's call sees 1.
    getppids = iter([500, 1])
    kills: list[tuple[int, int]] = []

    with (
        patch.object(supervisor.os, "getppid", side_effect=lambda: next(getppids)),
        patch.object(supervisor.threading, "Thread", _CaptureThread),
        patch.object(supervisor.time, "sleep", lambda _s: None),
        patch.object(supervisor.os, "getpid", return_value=4242),
        patch.object(
            supervisor.os, "kill", side_effect=lambda pid, sig: kills.append((pid, sig))
        ),
    ):
        supervisor._install_orphan_watchdog(poll_s=0.0)
        watch = captured["target"]
        watch()  # run one watch iteration synchronously

    assert kills == [(4242, supervisor.signal.SIGTERM)]
