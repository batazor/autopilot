#!/usr/bin/env python3
"""Benchmark Android screen-capture methods against the current `adb screencap` path.

Compares:
  - adb_screencap   `adb exec-out screencap -p` (current production path)
  - minicap         JPEG stream over a forwarded TCP socket
                    (https://github.com/DeviceFarmer/minicap)

Usage:
  uv run python scripts/bench_screencap.py --serial RF8RC00M8MF
  uv run python scripts/bench_screencap.py --serial RF8RC00M8MF --frames 200
  uv run python scripts/bench_screencap.py --serial RF8RC00M8MF --method adb
  uv run python scripts/bench_screencap.py --serial RF8RC00M8MF --install-minicap
  uv run python scripts/bench_screencap.py --serial RF8RC00M8MF \\
      --template references/workers_icon.png

Minicap binaries are downloaded from DeviceFarmer/minicap-prebuilt and pushed
to /data/local/tmp on first run (or via --install-minicap).
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterator


def _resolve_adb() -> str:
    for candidate in ("/opt/homebrew/bin/adb", "/usr/local/bin/adb"):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("adb")
    return found or "adb"


ADB_BIN = _resolve_adb()
MINICAP_REPO = "https://raw.githubusercontent.com/DeviceFarmer/minicap/master"
DEVICE_TMP = "/data/local/tmp"
MINICAP_PORT = 1313
DEFAULT_FRAMES = 50
TARGET_SIZE = (720, 1280)  # what the bot actually consumes (db/devices.yaml display.size)
SAMPLE_DIR = Path("/tmp/bench_screencap")


# ---------------------------------------------------------------------------
# adb helpers
# ---------------------------------------------------------------------------


def adb(
    args: list[str],
    *,
    serial: str | None = None,
    timeout: float = 10.0,
    check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = [ADB_BIN]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=check)


def adb_text(args: list[str], *, serial: str | None = None) -> str:
    return adb(args, serial=serial).stdout.decode(errors="replace").strip()


# ---------------------------------------------------------------------------
# method: adb screencap
# ---------------------------------------------------------------------------


def capture_via_adb(serial: str | None) -> tuple[np.ndarray | None, float, int]:
    cmd = [ADB_BIN]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=10.0, check=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    data = proc.stdout
    if proc.returncode != 0 or not data.startswith(b"\x89PNG"):
        return None, elapsed_ms, len(data)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img, elapsed_ms, len(data)


# ---------------------------------------------------------------------------
# method: minicap
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def wm_size_override(serial: str | None, size: tuple[int, int]) -> Iterator[None]:
    """Match the bot's behavior: `adb shell wm size 720x1280` then `wm size reset`."""
    target = f"{size[0]}x{size[1]}"
    _, _, before = detect_device_props(serial)
    if before == size:
        print(f"[wm size] already {target} — no override needed")
        yield
        return
    print(f"[wm size] {before[0]}x{before[1]} → {target}")
    adb(["shell", "wm", "size", target], serial=serial)
    time.sleep(0.5)
    try:
        yield
    finally:
        print("[wm size] reset")
        with contextlib.suppress(Exception):
            adb(["shell", "wm", "size", "reset"], serial=serial, check=False)


def detect_device_props(serial: str | None) -> tuple[str, str, tuple[int, int]]:
    abi = adb_text(["shell", "getprop", "ro.product.cpu.abi"], serial=serial)
    sdk = adb_text(["shell", "getprop", "ro.build.version.sdk"], serial=serial)
    size_text = adb_text(["shell", "wm", "size"], serial=serial)
    # `wm size` prints both "Physical size: ..." and (when override set) "Override size: ...".
    # The override is the actual framebuffer the device renders into — that's what
    # minicap/screencap will produce. Prefer Override when present.
    physical = override = None
    for line in size_text.splitlines():
        line = line.strip()
        try:
            label, value = line.split(":", 1)
            w_str, h_str = value.strip().split("x")
            sz = (int(w_str), int(h_str))
        except (ValueError, IndexError):
            continue
        if label.lower().startswith("override"):
            override = sz
        elif label.lower().startswith("physical"):
            physical = sz
    size = override or physical or (720, 1280)
    return abi, sdk, size


def minicap_installed(serial: str | None) -> bool:
    result = adb(
        ["shell", "ls", f"{DEVICE_TMP}/minicap"], serial=serial, check=False
    )
    return result.returncode == 0 and b"No such" not in result.stdout


def install_minicap(serial: str | None) -> None:
    abi, sdk, _ = detect_device_props(serial)
    print(f"  device: abi={abi}  sdk={sdk}")
    bin_url = f"{MINICAP_REPO}/libs/{abi}/minicap"
    lib_url = (
        f"{MINICAP_REPO}/jni/minicap-shared/aosp/libs/android-{sdk}/{abi}/minicap.so"
    )
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    bin_local = SAMPLE_DIR / "minicap"
    lib_local = SAMPLE_DIR / "minicap.so"
    print(f"  downloading {bin_url}")
    urllib.request.urlretrieve(bin_url, bin_local)
    print(f"  downloading {lib_url}")
    urllib.request.urlretrieve(lib_url, lib_local)
    print("  pushing to device")
    adb(["push", str(bin_local), f"{DEVICE_TMP}/minicap"], serial=serial)
    adb(["push", str(lib_local), f"{DEVICE_TMP}/minicap.so"], serial=serial)
    adb(["shell", "chmod", "755", f"{DEVICE_TMP}/minicap"], serial=serial)
    print("  installed")


@dataclass
class MinicapServer:
    serial: str | None
    proc: subprocess.Popen
    port: int
    width: int
    height: int

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.proc.terminate()
            self.proc.wait(timeout=2)
        with contextlib.suppress(Exception):
            adb(
                ["forward", "--remove", f"tcp:{self.port}"],
                serial=self.serial,
                check=False,
            )
        with contextlib.suppress(Exception):
            adb(["shell", "pkill", "-f", "minicap"], serial=self.serial, check=False)


def start_minicap(serial: str | None, port: int = MINICAP_PORT) -> MinicapServer:
    if not minicap_installed(serial):
        print("[minicap] not installed on device — installing")
        install_minicap(serial)
    # Ensure no stale instance.
    with contextlib.suppress(Exception):
        adb(["shell", "pkill", "-f", "minicap"], serial=serial, check=False)
    _, _, (w, h) = detect_device_props(serial)
    vw, vh = TARGET_SIZE
    p_arg = f"{w}x{h}@{vw}x{vh}/0"
    cmd = [ADB_BIN]
    if serial:
        cmd += ["-s", serial]
    cmd += [
        "shell",
        f"LD_LIBRARY_PATH={DEVICE_TMP}",
        f"{DEVICE_TMP}/minicap",
        "-P",
        p_arg,
    ]
    print(f"[minicap] starting: {p_arg}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(0.6)
    adb(
        ["forward", f"tcp:{port}", "localabstract:minicap"],
        serial=serial,
    )
    time.sleep(0.3)
    if proc.poll() is not None:
        err = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
        detail = err.strip() or "(no stderr)"
        msg = f"minicap exited immediately: {detail}"
        raise RuntimeError(msg)
    return MinicapServer(serial=serial, proc=proc, port=port, width=w, height=h)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            msg = f"socket closed (wanted {n}, got {len(buf)})"
            raise RuntimeError(msg)
        buf.extend(chunk)
    return bytes(buf)


def open_minicap_socket(port: int = MINICAP_PORT) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    # Banner: read first 2 bytes to learn its declared length, then drain the rest.
    head = _recv_exact(sock, 2)
    banner_size = head[1]
    if banner_size > 2:
        _recv_exact(sock, banner_size - 2)
    return sock


def capture_via_minicap(sock: socket.socket) -> tuple[np.ndarray | None, float, int]:
    t0 = time.perf_counter()
    size_bytes = _recv_exact(sock, 4)
    size = struct.unpack("<I", size_bytes)[0]
    jpeg = _recv_exact(sock, size)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img, elapsed_ms, len(jpeg)


# ---------------------------------------------------------------------------
# benchmark loop + reporting
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    method: str
    latencies_ms: list[float] = field(default_factory=list)
    raw_bytes: list[int] = field(default_factory=list)
    failures: int = 0
    sample: np.ndarray | None = None
    template_scores: list[float] = field(default_factory=list)

    def report(self) -> None:
        if not self.latencies_ms:
            print(f"\n=== {self.method} === no successful frames ({self.failures} failures)")
            return
        lats = sorted(self.latencies_ms)
        mean = statistics.mean(lats)
        p50 = lats[len(lats) // 2]
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
        p99 = lats[min(len(lats) - 1, int(len(lats) * 0.99))]
        fps = 1000.0 / mean if mean > 0 else 0
        avg_kb = statistics.mean(self.raw_bytes) / 1024 if self.raw_bytes else 0
        print(f"\n=== {self.method} ({len(lats)} frames, {self.failures} failed) ===")
        print(f"  mean   {mean:7.2f} ms     ({fps:5.1f} fps theoretical max)")
        print(f"  p50    {p50:7.2f} ms")
        print(f"  p95    {p95:7.2f} ms")
        print(f"  p99    {p99:7.2f} ms")
        print(f"  min    {lats[0]:7.2f} ms")
        print(f"  max    {lats[-1]:7.2f} ms")
        print(f"  raw    {avg_kb:7.1f} KB/frame  (network payload)")
        if self.template_scores:
            scores = self.template_scores
            print(
                f"  match  mean={statistics.mean(scores):.4f}  "
                f"min={min(scores):.4f}  max={max(scores):.4f}  "
                f"n={len(scores)}"
            )


def run_adb_benchmark(
    serial: str | None, n_frames: int, template: np.ndarray | None
) -> BenchResult:
    print(f"\n[adb_screencap] capturing {n_frames} frames...")
    r = BenchResult("adb_screencap")
    for i in range(n_frames):
        img, ms, size = capture_via_adb(serial)
        if img is None:
            r.failures += 1
            if r.failures <= 3:
                print(f"  frame {i}: failed")
            continue
        r.latencies_ms.append(ms)
        r.raw_bytes.append(size)
        if r.sample is None:
            r.sample = img.copy()
        if template is not None:
            r.template_scores.append(_match_score(img, template))
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{n_frames}  last={ms:.1f}ms")
    return r


def run_minicap_benchmark(
    serial: str | None, n_frames: int, template: np.ndarray | None, port: int
) -> BenchResult:
    r = BenchResult("minicap")
    server = start_minicap(serial, port=port)
    sock: socket.socket | None = None
    try:
        sock = open_minicap_socket(port=port)
        print(f"[minicap] capturing {n_frames} frames...")
        # First frame after connect can be slow — discard it as warmup.
        with contextlib.suppress(Exception):
            capture_via_minicap(sock)
        for i in range(n_frames):
            img, ms, size = capture_via_minicap(sock)
            if img is None:
                r.failures += 1
                continue
            r.latencies_ms.append(ms)
            r.raw_bytes.append(size)
            if r.sample is None:
                r.sample = img.copy()
            if template is not None:
                r.template_scores.append(_match_score(img, template))
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{n_frames}  last={ms:.1f}ms")
    finally:
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()
        server.close()
    return r


def _match_score(img: np.ndarray, template: np.ndarray) -> float:
    if img.shape[0] < template.shape[0] or img.shape[1] < template.shape[1]:
        return float("nan")
    res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return float(max_val)


def save_samples(results: list[BenchResult]) -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        if r.sample is None:
            continue
        path = SAMPLE_DIR / f"sample_{r.method}.png"
        cv2.imwrite(str(path), r.sample)
        print(f"  saved {path}")


def print_comparison(results: list[BenchResult]) -> None:
    valid = [r for r in results if r.latencies_ms]
    if len(valid) < 2:
        return
    print("\n=== comparison ===")
    print(f"  {'method':<16} {'p50 ms':>8} {'fps':>6} {'KB/frame':>10}")
    for r in valid:
        p50 = sorted(r.latencies_ms)[len(r.latencies_ms) // 2]
        mean = statistics.mean(r.latencies_ms)
        fps = 1000.0 / mean if mean > 0 else 0
        kb = statistics.mean(r.raw_bytes) / 1024 if r.raw_bytes else 0
        print(f"  {r.method:<16} {p50:8.2f} {fps:6.1f} {kb:10.1f}")
    fastest = min(valid, key=lambda r: statistics.mean(r.latencies_ms))
    slowest = max(valid, key=lambda r: statistics.mean(r.latencies_ms))
    if fastest is not slowest:
        ratio = statistics.mean(slowest.latencies_ms) / statistics.mean(
            fastest.latencies_ms
        )
        print(f"\n  {fastest.method} is {ratio:.1f}× faster than {slowest.method}")


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--serial", help="adb device serial (e.g., RF8RC00M8MF)")
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    ap.add_argument(
        "--method",
        choices=["adb", "minicap", "both"],
        default="both",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=MINICAP_PORT,
        help=f"local TCP port for minicap forward (default {MINICAP_PORT})",
    )
    ap.add_argument(
        "--template",
        type=Path,
        help="optional reference crop (PNG) — run template matching on every captured frame",
    )
    ap.add_argument(
        "--install-minicap",
        action="store_true",
        help="download + push minicap binaries and exit",
    )
    args = ap.parse_args()

    if args.install_minicap:
        install_minicap(args.serial)
        return 0

    template: np.ndarray | None = None
    if args.template:
        template = cv2.imread(str(args.template), cv2.IMREAD_COLOR)
        if template is None:
            print(f"error: could not read template {args.template}", file=sys.stderr)
            return 2
        print(f"template: {args.template} ({template.shape[1]}x{template.shape[0]})")

    results: list[BenchResult] = []
    with wm_size_override(args.serial, TARGET_SIZE):
        if args.method in ("adb", "both"):
            results.append(run_adb_benchmark(args.serial, args.frames, template))
        if args.method in ("minicap", "both"):
            try:
                results.append(
                    run_minicap_benchmark(
                        args.serial, args.frames, template, args.port
                    )
                )
            except Exception as exc:
                print(f"[minicap] benchmark failed: {exc}", file=sys.stderr)

    for r in results:
        r.report()
    print("\n[samples]")
    save_samples(results)
    print_comparison(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
