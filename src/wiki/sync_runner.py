"""Run wiki sync subprocesses and parse stdout into structured events."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

_PROGRESS_RE = re.compile(r"progress:\s*(\d+)\s*/\s*(\d+)")


@dataclass(frozen=True)
class SyncScriptSpec:
    key: str
    label: str
    script_rel: str
    args: tuple[str, ...] = ()
    progress_total_hint: int | None = None


SYNC_SCRIPT_SPECS: dict[str, SyncScriptSpec] = {
    "buildings": SyncScriptSpec("buildings", "Sync buildings", "cmd/sync_buildings_wiki.py"),
    "heroes": SyncScriptSpec("heroes", "Sync heroes", "cmd/sync_heroes_wiki.py"),
    "items": SyncScriptSpec(
        "items",
        "Sync items",
        "cmd/sync_items_wiki.py",
        progress_total_hint=405,
    ),
    "images": SyncScriptSpec(
        "images",
        "Download wiki images (all)",
        "cmd/download_wiki_images.py",
        args=("all",),
    ),
    "balance_sheet": SyncScriptSpec(
        "balance_sheet",
        "Sync balance sheet (heroes levels + gear + enhancement)",
        "cmd/sync_balance_sheet.py",
    ),
}


def get_sync_spec(script_key: str) -> SyncScriptSpec:
    key = script_key.strip()
    try:
        return SYNC_SCRIPT_SPECS[key]
    except KeyError as exc:
        msg = f"unknown sync script: {script_key}"
        raise KeyError(msg) from exc


def _build_command(spec: SyncScriptSpec, *, repo: Path) -> list[str]:
    script = (repo / spec.script_rel).resolve()
    if not script.is_file():
        msg = f"script not found: {spec.script_rel}"
        raise FileNotFoundError(msg)
    return [sys.executable, str(script), *spec.args]


def iter_sync_events(spec: SyncScriptSpec, *, repo: Path) -> Iterator[dict[str, Any]]:
    """Yield NDJSON-friendly event dicts while a sync subprocess runs."""
    started = time.time()
    try:
        cmd = _build_command(spec, repo=repo)
    except FileNotFoundError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {
        "type": "start",
        "key": spec.key,
        "label": spec.label,
        "command": cmd,
        "progress_total_hint": spec.progress_total_hint,
    }

    done = 0
    total = spec.progress_total_hint or 0
    summary = ""

    proc = subprocess.Popen(
        cmd,
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue
        yield {"type": "line", "text": line}

        m = _PROGRESS_RE.search(line)
        if m:
            done = int(m.group(1))
            total = int(m.group(2))
            yield {"type": "progress", "done": done, "total": total}

        if line.startswith(("updated ", "downloaded ")):
            summary = line

    exit_code = proc.wait()
    elapsed = time.time() - started
    yield {
        "type": "done",
        "exit_code": exit_code,
        "elapsed": round(elapsed, 2),
        "summary": summary or "(no summary line found)",
        "done": done,
        "total": total,
        "command": cmd,
    }


def encode_ndjson(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


async def aiter_sync_ndjson(spec: SyncScriptSpec, *, repo: Path) -> AsyncIterator[bytes]:
    """Stream sync stdout as NDJSON without blocking the event loop."""
    try:
        cmd = _build_command(spec, repo=repo)
    except FileNotFoundError as exc:
        yield encode_ndjson({"type": "error", "message": str(exc)})
        return

    started = time.time()
    yield encode_ndjson(
        {
            "type": "start",
            "key": spec.key,
            "label": spec.label,
            "command": cmd,
            "progress_total_hint": spec.progress_total_hint,
        }
    )

    done = 0
    total = spec.progress_total_hint or 0
    summary = ""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        yield encode_ndjson({"type": "line", "text": line})

        m = _PROGRESS_RE.search(line)
        if m:
            done = int(m.group(1))
            total = int(m.group(2))
            yield encode_ndjson({"type": "progress", "done": done, "total": total})

        if line.startswith(("updated ", "downloaded ")):
            summary = line

    exit_code = await proc.wait()
    elapsed = time.time() - started
    yield encode_ndjson(
        {
            "type": "done",
            "exit_code": exit_code,
            "elapsed": round(elapsed, 2),
            "summary": summary or "(no summary line found)",
            "done": done,
            "total": total,
            "command": cmd,
        }
    )
