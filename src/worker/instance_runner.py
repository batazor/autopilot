"""Isolated single-instance worker entrypoint.

Runs the worker session for **one** instance only — no scheduler, no other
instances. This is the "run a scenario in isolation" primitive behind the
fish-detect Play button: instead of spawning the full supervisor fleet (every
device + cron scheduler), we start just this device's worker and enqueue the one
scenario we want it to play.

It reuses :func:`worker.supervisor._worker_process` verbatim, so the device
session, overlay engine, queue consumption and approval gating behave exactly as
they do under the supervisor — only the blast radius is one instance.

Usage::

    python -m worker.instance_runner <instance_id>
"""

from __future__ import annotations

import os
import sys

from config.loader import load_settings, set_settings
from worker.supervisor import _worker_process


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    instance_id = (args[0] if args else os.environ.get("WOS_INSTANCE_ID", "")).strip()
    if not instance_id:
        msg = "usage: python -m worker.instance_runner <instance_id>"
        raise SystemExit(msg)

    settings = load_settings()
    set_settings(settings)
    cfg = next(
        (inst for inst in settings.instances if inst.instance_id == instance_id),
        None,
    )
    if cfg is None:
        known = ", ".join(inst.instance_id for inst in settings.instances) or "(none)"
        msg = f"unknown instance: {instance_id!r} (configured: {known})"
        raise SystemExit(msg)

    # Stable id for observability, mirroring the supervisor's child stamping.
    os.environ.setdefault("WOS_INSTANCE_ID", instance_id)
    _worker_process(cfg)


if __name__ == "__main__":
    main()
