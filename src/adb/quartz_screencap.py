"""macOS WindowServer screenshot capture for visible BlueStacks windows.

The bot still sends input through ADB, but screenshots can be faster when read
from the host window. Returned frames are normalized to the ADB framebuffer
coordinate space (720x1280 BGR) so existing matchers do not need to know the
capture source.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import select
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_QUARTZ_TARGET_SIZE = (720, 1280)
_DEFAULT_BLUESTACKS_TOP_CHROME_PX = 65
_SCK_HELPER_RESPONSE_TIMEOUT_SECONDS = 5.0
_WINDOW_CACHE: dict[tuple[str, str, int | None], int] = {}
_SCK_HELPER_LOCK = threading.Lock()
_SCK_HELPER: subprocess.Popen[bytes] | None = None


@dataclass(frozen=True)
class QuartzWindow:
    window_id: int
    owner: str
    title: str
    layer: int
    x: int
    y: int
    width: int
    height: int


def quartz_screencap_bgr(
    *,
    instance_id: str,
    quartz_window_id: int | None = None,
    quartz_window_title: str = "",
    quartz_crop: tuple[int, int, int, int] | None = None,
    target_size: tuple[int, int] = DEFAULT_QUARTZ_TARGET_SIZE,
) -> np.ndarray:
    """Capture a BlueStacks window via macOS ``screencapture`` and normalize to BGR.

    ``quartz_crop`` is ``(x, y, width, height)`` in the captured window PNG.
    When omitted, the crop assumes a portrait BlueStacks window with a small
    top chrome strip and preserves the 720:1280 framebuffer aspect ratio.
    """
    sck_error: Exception | None = None
    try:
        return _sck_screencap_bgr(
            instance_id=instance_id,
            quartz_window_id=quartz_window_id,
            quartz_window_title=quartz_window_title,
            quartz_crop=quartz_crop,
            target_size=target_size,
        )
    except Exception as exc:
        sck_error = exc
        logger.debug(
            "ScreenCaptureKit capture failed for %s; falling back to screencapture: %s",
            instance_id,
            exc,
        )

    window_id = quartz_window_id or _discover_window_id(
        instance_id=instance_id,
        quartz_window_title=quartz_window_title,
    )
    try:
        try:
            return _capture_window_id_bgr(
                window_id,
                quartz_crop=quartz_crop,
                target_size=target_size,
            )
        except RuntimeError as exc:
            if sck_error is not None:
                msg = f"ScreenCaptureKit failed: {sck_error}; screencapture fallback failed: {exc}"
                raise RuntimeError(msg) from exc
            raise
    except RuntimeError as exc:
        if sck_error is not None:
            msg = f"ScreenCaptureKit failed: {sck_error}; screencapture fallback failed: {exc}"
            raise RuntimeError(msg) from exc
        raise


def _capture_window_id_bgr(
    window_id: int,
    *,
    quartz_crop: tuple[int, int, int, int] | None,
    target_size: tuple[int, int],
) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        proc = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-l", str(window_id), f.name],
            capture_output=True,
            check=False,
            timeout=5.0,
        )
        if proc.returncode != 0:
            detail = (
                proc.stderr.decode(errors="replace").strip()
                or proc.stdout.decode(errors="replace").strip()
                or "unknown error"
            )
            msg = f"Quartz screencapture failed for window {window_id}: {detail}"
            raise RuntimeError(msg)
        image = cv2.imread(f.name, cv2.IMREAD_COLOR)
    if image is None:
        msg = f"Quartz screencapture returned an unreadable PNG for window {window_id}"
        raise RuntimeError(msg)
    crop = quartz_crop or _auto_blustacks_content_crop(image.shape[1], image.shape[0], target_size)
    x, y, w, h = _clamp_crop(crop, image.shape[1], image.shape[0])
    image = image[y : y + h, x : x + w]
    target_w, target_h = target_size
    return cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_AREA)


def _read_helper_line_with_timeout(helper: subprocess.Popen[bytes]) -> bytes:
    assert helper.stdout is not None
    ready, _, _ = select.select([helper.stdout], [], [], _SCK_HELPER_RESPONSE_TIMEOUT_SECONDS)
    if not ready:
        msg = f"ScreenCaptureKit helper timed out after {_SCK_HELPER_RESPONSE_TIMEOUT_SECONDS:.1f}s"
        raise TimeoutError(msg)
    return helper.stdout.readline()


def _read_helper_bytes_with_timeout(helper: subprocess.Popen[bytes], byte_count: int) -> bytes:
    assert helper.stdout is not None
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining > 0:
        ready, _, _ = select.select([helper.stdout], [], [], _SCK_HELPER_RESPONSE_TIMEOUT_SECONDS)
        if not ready:
            got = byte_count - remaining
            msg = (
                f"ScreenCaptureKit helper timed out while reading PNG "
                f"after {got}/{byte_count} bytes"
            )
            raise TimeoutError(msg)
        chunk = helper.stdout.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_helper_trailing_newline(helper: subprocess.Popen[bytes]) -> None:
    assert helper.stdout is not None
    ready, _, _ = select.select([helper.stdout], [], [], _SCK_HELPER_RESPONSE_TIMEOUT_SECONDS)
    if ready:
        helper.stdout.read(1)


def _sck_screencap_bgr(
    *,
    instance_id: str,
    quartz_window_id: int | None,
    quartz_window_title: str,
    quartz_crop: tuple[int, int, int, int] | None,
    target_size: tuple[int, int],
) -> np.ndarray:
    helper = _ensure_sck_helper()
    target_w, target_h = target_size
    request = {
        "instance_id": instance_id,
        "window_id": quartz_window_id,
        "window_title": quartz_window_title,
        "crop": list(quartz_crop) if quartz_crop is not None else None,
        "target_width": target_w,
        "target_height": target_h,
    }
    payload = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    with _SCK_HELPER_LOCK:
        try:
            data = _request_sck_png_locked(helper, payload)
        except Exception:
            _restart_sck_helper_locked()
            raise

    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        msg = "ScreenCaptureKit helper returned an invalid PNG"
        raise RuntimeError(msg)
    if image.shape[1] != target_w or image.shape[0] != target_h:
        image = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_AREA)
    return image


def _request_sck_png_locked(helper: subprocess.Popen[bytes], payload: bytes) -> bytes:
    assert helper.stdin is not None
    assert helper.stdout is not None
    helper.stdin.write(payload)
    helper.stdin.flush()
    header_line = _read_helper_line_with_timeout(helper)
    if not header_line:
        stderr = _drain_helper_stderr(helper)
        msg = f"ScreenCaptureKit helper exited without response: {stderr or 'no stderr'}"
        raise RuntimeError(msg)
    header = json.loads(header_line.decode("utf-8"))
    if not header.get("ok"):
        msg = f"ScreenCaptureKit helper error: {header.get('error') or 'unknown error'}"
        raise RuntimeError(msg)
    byte_count = int(header["bytes"])
    data = _read_helper_bytes_with_timeout(helper, byte_count)
    _read_helper_trailing_newline(helper)
    if len(data) != byte_count:
        msg = f"ScreenCaptureKit helper returned {len(data)} bytes, expected {byte_count}"
        raise RuntimeError(msg)
    return data


def _ensure_sck_helper() -> subprocess.Popen[bytes]:
    global _SCK_HELPER
    with _SCK_HELPER_LOCK:
        if _SCK_HELPER is not None and _SCK_HELPER.poll() is None:
            return _SCK_HELPER
        _SCK_HELPER = _start_sck_helper()
        return _SCK_HELPER


def _restart_sck_helper_locked() -> None:
    global _SCK_HELPER
    if _SCK_HELPER is not None:
        with contextlib.suppress(Exception):
            _SCK_HELPER.kill()
        with contextlib.suppress(Exception):
            _SCK_HELPER.wait(timeout=1.0)
    _SCK_HELPER = None


def _start_sck_helper() -> subprocess.Popen[bytes]:
    bin_path = _ensure_sck_helper_binary()
    return subprocess.Popen(
        [str(bin_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _ensure_sck_helper_binary() -> Path:
    root = repo_root()
    source = root / "src" / "adb" / "sck_capture_helper.swift"
    cache_dir = root / ".cache" / "sck"
    cache_dir.mkdir(parents=True, exist_ok=True)
    binary = cache_dir / "sck_capture_helper"
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    tmp = binary.with_suffix(".tmp")
    proc = subprocess.run(
        ["swiftc", "-parse-as-library", str(source), "-o", str(tmp)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown error").strip()
        msg = f"Failed to compile ScreenCaptureKit helper: {detail}"
        raise RuntimeError(msg)
    tmp.replace(binary)
    return binary


def _drain_helper_stderr(helper: subprocess.Popen[bytes]) -> str:
    if helper.stderr is None:
        return ""
    if helper.poll() is None:
        return ""
    try:
        return helper.stderr.read(4096).decode(errors="replace").strip()
    except Exception:
        return ""


def _discover_window_id(
    *,
    instance_id: str,
    quartz_window_title: str,
) -> int:
    key = _cache_key(instance_id, quartz_window_title, None)
    cached = _WINDOW_CACHE.get(key)
    if cached is not None:
        return cached
    windows = _list_quartz_windows()
    window = _pick_window(
        windows,
        instance_id=instance_id,
        quartz_window_title=quartz_window_title,
    )
    _WINDOW_CACHE[key] = window.window_id
    logger.info(
        "Quartz capture for %s: selected window id=%s owner=%r title=%r bounds=%dx%d",
        instance_id,
        window.window_id,
        window.owner,
        window.title,
        window.width,
        window.height,
    )
    return window.window_id


def _cache_key(instance_id: str, quartz_window_title: str, quartz_window_id: int | None) -> tuple[str, str, int | None]:
    return (str(instance_id or "").strip(), str(quartz_window_title or "").strip(), quartz_window_id)


def _list_quartz_windows() -> list[QuartzWindow]:
    script = r"""
import Foundation
import CoreGraphics

let opts: CGWindowListOption = [.optionAll, .excludeDesktopElements]
let windows = (CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]]) ?? []
var rows: [[String: Any]] = []
for w in windows {
    let bounds = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    rows.append([
        "id": w[kCGWindowNumber as String] as? Int ?? 0,
        "owner": w[kCGWindowOwnerName as String] as? String ?? "",
        "title": w[kCGWindowName as String] as? String ?? "",
        "layer": w[kCGWindowLayer as String] as? Int ?? -1,
        "x": (bounds["X"] as? NSNumber)?.intValue ?? 0,
        "y": (bounds["Y"] as? NSNumber)?.intValue ?? 0,
        "width": (bounds["Width"] as? NSNumber)?.intValue ?? 0,
        "height": (bounds["Height"] as? NSNumber)?.intValue ?? 0,
    ])
}
let data = try! JSONSerialization.data(withJSONObject: rows)
print(String(data: data, encoding: .utf8)!)
"""
    proc = subprocess.run(
        ["/usr/bin/swift", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
        timeout=10.0,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown error").strip()
        msg = f"Quartz window discovery failed: {detail}"
        raise RuntimeError(msg)
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        msg = "Quartz window discovery returned invalid JSON"
        raise RuntimeError(msg) from exc
    return [_window_from_row(row) for row in rows if isinstance(row, dict)]


def _window_from_row(row: dict[str, Any]) -> QuartzWindow:
    return QuartzWindow(
        window_id=int(row.get("id") or 0),
        owner=str(row.get("owner") or ""),
        title=str(row.get("title") or ""),
        layer=int(row.get("layer") or 0),
        x=int(row.get("x") or 0),
        y=int(row.get("y") or 0),
        width=int(row.get("width") or 0),
        height=int(row.get("height") or 0),
    )


def _pick_window(
    windows: list[QuartzWindow],
    *,
    instance_id: str,
    quartz_window_title: str,
) -> QuartzWindow:
    visible = [w for w in windows if w.window_id > 0 and w.width >= 300 and w.height >= 300]
    title = quartz_window_title.strip().lower()
    if title:
        matches = [w for w in visible if title in w.title.lower() or title in w.owner.lower()]
        if matches:
            return _largest_layer0(matches)
        msg = f"Quartz window title {quartz_window_title!r} not found"
        raise RuntimeError(msg)

    air_title = _default_bluestacks_air_title(instance_id)
    if air_title:
        matches = [w for w in visible if w.owner == "BlueStacks" and w.title == air_title]
        if matches:
            return _largest_layer0(matches)

    matches = [
        w
        for w in visible
        if "bluestacks" in w.owner.lower()
        and "keymap" not in w.title.lower()
        and (w.title.strip() or w.layer == 0)
    ]
    if not matches:
        msg = "No BlueStacks Quartz window found"
        raise RuntimeError(msg)
    return _largest_layer0(matches)


def _largest_layer0(windows: list[QuartzWindow]) -> QuartzWindow:
    layer0 = [w for w in windows if w.layer == 0] or windows
    return max(layer0, key=lambda w: w.width * w.height)


def _default_bluestacks_air_title(instance_id: str) -> str:
    match = re.fullmatch(r"bs(\d+)", str(instance_id or "").strip().lower())
    if not match:
        return ""
    return f"BlueStacks Air {int(match.group(1)) - 1}"


def _auto_blustacks_content_crop(
    source_w: int,
    source_h: int,
    target_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    y = min(_DEFAULT_BLUESTACKS_TOP_CHROME_PX, max(0, source_h - 1))
    h = max(1, source_h - y)
    w = int(round(h * target_ratio))
    if w > source_w:
        w = source_w
        h = int(round(w / target_ratio))
        y = max(0, source_h - h)
    return (0, y, w, h)


def _clamp_crop(
    crop: tuple[int, int, int, int],
    source_w: int,
    source_h: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = crop
    x = max(0, min(int(x), max(0, source_w - 1)))
    y = max(0, min(int(y), max(0, source_h - 1)))
    w = max(1, min(int(w), source_w - x))
    h = max(1, min(int(h), source_h - y))
    return x, y, w, h
