#!/usr/bin/env python3
"""Sample host CPU per component — the repeatable before/after for perf work.

Replaces the ad-hoc ``sample`` + ``ps`` pass used for the deep-idle work
(commit ``10b76519``), so a change's contribution is a number instead of a
recollection. Groups every process on the host into the buckets that matter
here — emulators, bot worker/scheduler/supervisor, API, Next.js, scrcpy, adb —
and reports each bucket's CPU as a percentage of one core (so 233% means 2.33
cores busy) alongside the machine's total capacity.

Usage:

    uv run python scripts/bench_cpu.py                    # 10s window
    uv run python scripts/bench_cpu.py --seconds 30
    uv run python scripts/bench_cpu.py --seconds 30 --json

``--per-process`` additionally lists the individual processes inside each
bucket, which is how you tell "one emulator is pegged" from "six are warm".
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path, PurePath

import psutil

REPO = Path(__file__).resolve().parents[1]

# Console-script entrypoints from pyproject's [project.scripts]. Matched on the
# *exact basename* of a cmdline token, never as a substring — `.../bin/botctl`
# contains `/bin/bot`, so substring matching would file the agent CLI as the
# supervisor. This is how `uv run bot` was missed entirely: the bucket looked
# for "worker.supervisor" while the real cmdline is `.../.venv/bin/bot`.
_CONSOLE_SCRIPT_BUCKETS: dict[str, str] = {
    "bot": "bot:supervisor",
    "play": "bot:launcher",
    "api": "api",
}

# Bucket name -> ordered (needle, ...) matched against the process cmdline
# (lowercased, falling back to the process name). First bucket to match wins,
# so the more specific patterns are listed first.
_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("emulator", ("bluestacks", "qemu-system", "ldplayer", "nox", "memu")),
    ("bot:watchdog", ("worker.game_health_watchdog", "game_health_watchdog")),
    ("bot:supervisor", ("-m worker.supervisor", "worker.supervisor")),
    # Spawned children (workers + scheduler) carry a generic multiprocessing
    # cmdline, so they can only be attributed by parentage — see _bucket_of.
    ("bot:child", ("multiprocessing.spawn",)),
    ("bot:resource-tracker", ("multiprocessing.resource_tracker",)),
    ("api", ("uvicorn", "api.main")),
    ("next", ("next dev", "next start", "next-server", "pnpm dev")),
    ("scrcpy", ("scrcpy",)),
    ("adb", ("platform-tools/adb", "/adb ", "adb fork-server", "adb -L")),
    ("redis", ("redis-server",)),
    # Agent tooling — bucketed explicitly so it doesn't land in `unbucketed`.
    # Matched on the venv-relative path so the editor/agent processes that
    # merely *spawn* botctl (and name it in their own cmdline) aren't swept in.
    ("agent-cli", ("/bin/botctl",)),
)


def _tokens_of(proc: psutil.Process) -> list[str]:
    try:
        return [str(p).lower() for p in (proc.info.get("cmdline") or [])]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def _cmdline_of(proc: psutil.Process) -> str:
    """Lowercased cmdline, falling back to the process name."""
    try:
        parts = proc.info.get("cmdline") or []
        return (" ".join(parts) or (proc.info.get("name") or "")).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _bucket_of(cmdline: str, tokens: list[str]) -> str | None:
    if not cmdline:
        return None
    for token in tokens:
        bucket = _CONSOLE_SCRIPT_BUCKETS.get(PurePath(token).name)
        if bucket is not None:
            return bucket
    for bucket, needles in _BUCKETS:
        if any(needle in cmdline for needle in needles):
            return bucket
    return None


def _looks_like_ours(cmdline: str) -> bool:
    """True for a process that belongs to this repo but matched no bucket.

    Reported rather than dropped: a measurement tool that silently omits
    processes understates exactly the thing it is used to argue about.

    Deliberately keyed on *running our virtualenv's interpreter*, not on the
    repo path appearing anywhere — Spotlight's ``mdworker`` and the editor's
    language servers mention the repo path constantly and are not ours.
    """
    return f"{REPO}/.venv/".lower() in cmdline


def _label_of(proc: psutil.Process, cmdline: str) -> str:
    """Short human label — the emulator instance name where we can find one."""
    marker = "--instance "
    if marker in cmdline:
        return cmdline.split(marker, 1)[1].split()[0]
    try:
        return proc.info.get("name") or f"pid {proc.pid}"
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return f"pid {proc.pid}"


def sample(seconds: float) -> tuple[dict[str, list[tuple[str, int, float]]], float]:
    """Sample CPU over ``seconds`` and return (bucket -> rows, elapsed).

    Each row is ``(label, pid, cpu_percent)``. ``cpu_percent`` is relative to a
    single core, matching ``top``'s convention.
    """
    tracked: list[tuple[psutil.Process, str, str]] = []
    self_pid = os.getpid()
    for proc in psutil.process_iter(["name", "cmdline"]):
        if proc.pid == self_pid:
            continue  # the benchmark must not measure itself
        cmdline = _cmdline_of(proc)
        bucket = _bucket_of(cmdline, _tokens_of(proc))
        if bucket is None:
            if not _looks_like_ours(cmdline):
                continue
            bucket = "unbucketed"
        try:
            proc.cpu_percent(None)  # prime the counter; first call returns 0.0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        tracked.append((proc, cmdline, bucket))

    started = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - started

    rows: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    for proc, cmdline, bucket in tracked:
        try:
            cpu = proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        # A spawned child is a worker or the scheduler; both descend from the
        # supervisor, so fold them into one bucket the reader recognises.
        if bucket == "bot:child":
            bucket = "bot:worker/scheduler"
        rows[bucket].append((_label_of(proc, cmdline), proc.pid, cpu))
    return rows, elapsed


def _print_table(
    rows: dict[str, list[tuple[str, int, float]]],
    elapsed: float,
    *,
    per_process: bool,
) -> None:
    cores = psutil.cpu_count() or 1
    totals = {
        bucket: sum(cpu for _, _, cpu in entries) for bucket, entries in rows.items()
    }
    grand = sum(totals.values())

    print(f"window: {elapsed:.1f}s   machine: {cores} logical cores = {cores * 100}%\n")
    print(f"{'component':24} {'CPU%':>8} {'procs':>6} {'of machine':>11}")
    print("-" * 52)
    for bucket, total in sorted(totals.items(), key=lambda kv: -kv[1]):
        share = total / (cores * 100) * 100
        print(f"{bucket:24} {total:8.1f} {len(rows[bucket]):6d} {share:10.1f}%")
        if per_process:
            for label, pid, cpu in sorted(rows[bucket], key=lambda r: -r[2]):
                print(f"    {label:>26} pid {pid:<7} {cpu:6.1f}")
    print("-" * 52)
    print(f"{'TOTAL tracked':24} {grand:8.1f} {'':6} {grand / (cores * 100) * 100:10.1f}%")
    if rows.get("unbucketed"):
        # Loud on purpose: an unrecognised repo process means the buckets have
        # drifted from reality and every total above is an undercount.
        print(
            f"\n! {len(rows['unbucketed'])} repo process(es) matched no bucket "
            f"— totals are incomplete. Run with --per-process to see them."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="sampling window in seconds (default: 10)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--per-process",
        action="store_true",
        help="also list individual processes within each component",
    )
    args = parser.parse_args(argv)

    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    rows, elapsed = sample(args.seconds)

    if args.json:
        cores = psutil.cpu_count() or 1
        payload = {
            "window_seconds": round(elapsed, 2),
            "logical_cores": cores,
            "machine_capacity_pct": cores * 100,
            "components": {
                bucket: {
                    "cpu_pct": round(sum(cpu for _, _, cpu in entries), 1),
                    "processes": [
                        {"label": label, "pid": pid, "cpu_pct": round(cpu, 1)}
                        for label, pid, cpu in sorted(entries, key=lambda r: -r[2])
                    ],
                }
                for bucket, entries in rows.items()
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_table(rows, elapsed, per_process=args.per_process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
