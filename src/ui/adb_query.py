"""Pure ADB-CLI helpers shared by Streamlit pages.

Lives outside ``ui/views/`` so multi-page UIs can import the parser/dedup logic
without executing another page's module-level Streamlit calls.
"""
from __future__ import annotations

import concurrent.futures as _cf
import subprocess

from adb.screencap import resolve_adb_executable
from ui.settings_state import get_ui_adb_bin


def parse_adb_devices(output: str) -> list[dict[str, str]]:
    """Parse ``adb devices -l`` into rows. Skips header and empty lines."""
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split(maxsplit=1)
        serial = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # Tokens after serial: "device product:foo model:bar device:baz transport_id:1"
        tokens = rest.split()
        state = tokens[0] if tokens else ""
        attrs: dict[str, str] = {}
        for tok in tokens[1:]:
            if ":" in tok:
                k, v = tok.split(":", 1)
                attrs[k] = v
        rows.append(
            {
                "serial": serial,
                "state": state,
                "model": attrs.get("model", ""),
                "product": attrs.get("product", ""),
                "device": attrs.get("device", ""),
                "transport_id": attrs.get("transport_id", ""),
            }
        )
    return rows


def dedupe_emulator_aliases(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop ``emulator-<N>`` rows when ``127.0.0.1:<N+1>`` is present.

    ADB ``start-server`` auto-scans console ports 5554-5585 and registers
    found emulators as ``emulator-<console_port>`` — but the same emulator is
    also reachable via its adbd port (console + 1). When both serials list
    the same device, the ``emulator-*`` row is just an alias.
    """
    network_ports: set[int] = set()
    for r in rows:
        s = str(r.get("serial", "") or "")
        if s.startswith("127.0.0.1:"):
            try:
                network_ports.add(int(s.split(":", 1)[1]))
            except (ValueError, IndexError):
                continue
    out: list[dict[str, str]] = []
    for r in rows:
        s = str(r.get("serial", "") or "")
        if s.startswith("emulator-"):
            try:
                console_port = int(s.split("-", 1)[1])
            except (ValueError, IndexError):
                out.append(r)
                continue
            if (console_port + 1) in network_ports:
                continue
        out.append(r)
    return out


def canonical_serial(s: str) -> str:
    """Collapse ``emulator-N`` and ``127.0.0.1:<N+1>`` to the same form."""
    s = (s or "").strip()
    if s.startswith("emulator-"):
        try:
            n = int(s.split("-", 1)[1])
            return f"127.0.0.1:{n + 1}"
        except (ValueError, IndexError):
            pass
    return s


def run_adb(args: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Run ADB with the UI-resolved binary; returns ``(rc, stdout, stderr)``."""
    resolved = resolve_adb_executable(get_ui_adb_bin())
    if resolved is None:
        return -1, "", f"adb binary not found: `{get_ui_adb_bin()}`"
    try:
        proc = subprocess.run(
            [resolved, *args], capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"`adb {' '.join(args)}` timed out after {timeout:.0f}s"
    except FileNotFoundError:
        return -1, "", f"could not execute `{resolved}`"
    return (
        proc.returncode,
        proc.stdout.decode(errors="replace").strip(),
        proc.stderr.decode(errors="replace").strip(),
    )


def port_scan_connect(start: int, end: int) -> tuple[list[int], list[int]]:
    """Run ``adb connect 127.0.0.1:<port>`` across ``[start, end]`` in parallel.

    Picks up emulators (BlueStacks / MuMu / LDPlayer / SDK) that the local ADB
    server doesn't know about yet — `adb devices` alone won't reattach a
    disconnected emulator. Returns ``(newly_connected, already_connected)``
    port lists; closed ports are dropped.
    """
    if end < start:
        return [], []
    ports = list(range(start, end + 1))

    def _connect(port: int) -> tuple[int, str, str]:
        return run_adb(["connect", f"127.0.0.1:{port}"], timeout=1.5)

    with _cf.ThreadPoolExecutor(max_workers=min(10, max(1, len(ports)))) as pool:
        results = list(zip(ports, pool.map(_connect, ports), strict=True))

    newly: list[int] = []
    already: list[int] = []
    for port, (rc, out, err) in results:
        text = f"{out} {err}".lower()
        if "already connected" in text:
            already.append(port)
        elif rc == 0 and "connected to" in text:
            newly.append(port)
    return newly, already


def live_serials() -> set[str]:
    """Canonical serials currently in ADB's ``device`` state.

    Returns an empty set if ``adb`` isn't reachable. Used by Streamlit pages
    to filter the configured instance list down to what's actually online.
    """
    return {
        serial
        for serial, state in adb_device_states().items()
        if state == "device"
    }


def adb_device_states() -> dict[str, str]:
    """Canonical serial -> ADB state (``device``, ``offline``, ``unauthorized``, …)."""
    rc, out, _ = run_adb(["devices", "-l"], timeout=5.0)
    if rc != 0:
        return {}
    rows = dedupe_emulator_aliases(parse_adb_devices(out))
    return {
        canonical_serial(r["serial"]): str(r.get("state") or "")
        for r in rows
        if r.get("serial")
    }
