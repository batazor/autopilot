"""Process bucketing for the CPU benchmark (scripts/bench_cpu.py).

The tool's whole purpose is to answer "how much CPU is the bot costing?", so a
process it fails to recognise is not a cosmetic bug — it silently understates
the number the answer is built on. That happened: the supervisor started as
``uv run bot`` matched no bucket and vanished from the table entirely, while the
totals still read as complete.

Cmdlines below are the real ones observed on a live machine.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENV = f"{_REPO_ROOT}/.venv/bin"


def _mod():
    spec = importlib.util.spec_from_file_location(
        "bench_cpu_test", _REPO_ROOT / "scripts" / "bench_cpu.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bucket(cmdline: str) -> str | None:
    m = _mod()
    return m._bucket_of(cmdline.lower(), [t.lower() for t in cmdline.split()])


def test_supervisor_started_via_console_script_is_found() -> None:
    """The regression: `uv run bot` produced no supervisor row at all."""
    assert _bucket(f"{_VENV}/python3 {_VENV}/bot") == "bot:supervisor"
    assert _bucket("uv run bot") == "bot:supervisor"


def test_supervisor_started_as_a_module_is_still_found() -> None:
    assert _bucket(f"{_VENV}/python3 -m worker.supervisor") == "bot:supervisor"


def test_botctl_is_not_mistaken_for_the_supervisor() -> None:
    """`/bin/botctl` contains `/bin/bot` — substring matching gets this wrong."""
    for cmd in (f"{_VENV}/python3 {_VENV}/botctl", f"{_VENV}/python3 {_VENV}/botctl-mcp"):
        assert _bucket(cmd) != "bot:supervisor", cmd


def test_spawned_workers_and_api_and_emulator() -> None:
    assert (
        _bucket(f"{_VENV}/python3 -c from multiprocessing.spawn import spawn_main; spawn_main()")
        == "bot:child"
    )
    assert _bucket(f"{_VENV}/python3 {_VENV}/api") == "api"
    assert _bucket("/Applications/BlueStacks.app/Contents/MacOS/BlueStacks --instance Tiramisu64_6") == "emulator"


def test_unrelated_processes_are_not_claimed_as_ours() -> None:
    """`unbucketed` must mean "ours but unrecognised", not "mentions the repo".

    Spotlight and editor language servers name the repo path constantly.
    """
    m = _mod()
    assert not m._looks_like_ours(
        f"/system/library/.../mdworker_shared -c mdworker -m com.apple.mdworker.shared {_REPO_ROOT}".lower()
    )
    assert m._looks_like_ours(f"{_VENV}/python3 {_VENV}/somenewtool".lower())
