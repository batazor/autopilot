"""Independent rolling screenshot refresher for IA Editor mode."""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import anyio
import cv2
import numpy as np
import redis

from adb.screencap import DEFAULT_ADB_BIN, adb_screencap_png
from config.loader import load_settings
from config.paths import repo_root
from config.reference_naming import reference_png_abs_path, rolling_preview_basename
from navigation.detector import ScreenDetector, ScreenName
from ocr.client import OcrClient

logger = logging.getLogger(__name__)

_THREAD_NAME = "wos-ia-preview-refresher"
_SCREEN_HISTORY_MAX = 5
_SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES = 3
_SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS = 2.0


def _existing_preview_thread() -> threading.Thread | None:
    for thread in threading.enumerate():
        if thread.name == _THREAD_NAME and thread.is_alive():
            return thread
    return None


def _write_png_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".ia-preview-", suffix=".png", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _png_to_bgr(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


async def _detect_screen_with_hint(
    detector: ScreenDetector,
    image_bgr: np.ndarray,
    hint: str | None,
) -> ScreenName:
    return await detector.detect_screen(image_bgr, hint=hint)


def _write_detected_screen(
    client: redis.Redis,
    *,
    instance_id: str,
    screen: str,
) -> None:
    state_key = f"wos:instance:{instance_id}:state"
    history_key = f"wos:instance:{instance_id}:screen_history"
    client.hset(state_key, "current_screen", screen)
    screen_s = str(screen or "").strip()
    if not screen_s:
        return
    head = client.lindex(history_key, 0)
    head_s = (head.decode() if isinstance(head, bytes) else str(head or "")).strip()
    if head_s == screen_s:
        return
    client.lpush(history_key, screen_s)
    client.ltrim(history_key, 0, _SCREEN_HISTORY_MAX - 1)


def _preview_loop() -> None:
    settings = load_settings()
    root = repo_root()
    adb_bin = settings.worker.adb_executable.strip() or DEFAULT_ADB_BIN
    interval = max(0.5, float(settings.worker.device_reference_snapshot_interval_seconds))
    instances = list(settings.instances)
    detector = ScreenDetector(OcrClient(settings))
    redis_client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    last_detected_screen: dict[str, str] = {}
    last_detected_screen_at: dict[str, float] = {}
    unknown_since: dict[str, float] = {}
    unknown_streak: dict[str, int] = {}

    logger.info(
        "IA preview refresher started for %d instance(s), interval=%.2fs",
        len(instances),
        interval,
    )
    last_error_at: dict[str, float] = {}
    while True:
        started = time.monotonic()
        for inst in instances:
            data, err = adb_screencap_png(adb_bin=adb_bin, serial=inst.bluestacks_window_title)
            if data is None:
                now = time.monotonic()
                last = last_error_at.get(inst.instance_id, 0.0)
                if now - last > 60.0:
                    logger.warning(
                        "IA preview refresher: screencap failed for %s (%s): %s",
                        inst.instance_id,
                        inst.bluestacks_window_title,
                        err,
                    )
                    last_error_at[inst.instance_id] = now
                continue
            path = reference_png_abs_path(
                root,
                rolling_preview_basename(inst.instance_id),
                inst.instance_id,
            )
            try:
                _write_png_atomic(path, data)
            except OSError:
                logger.exception(
                    "IA preview refresher: failed to write %s for %s",
                    path,
                    inst.instance_id,
                )
                continue

            image_bgr = _png_to_bgr(data)
            if image_bgr is None:
                logger.warning(
                    "IA preview refresher: failed to decode screenshot for %s",
                    inst.instance_id,
                )
                continue
            try:
                inst_id = inst.instance_id
                hint = last_detected_screen.get(inst_id) or None
                detected = anyio.run(_detect_screen_with_hint, detector, image_bgr, hint)
                if detected != ScreenName.UNKNOWN:
                    screen = str(detected)
                    last_detected_screen[inst_id] = screen
                    last_detected_screen_at[inst_id] = time.monotonic()
                    unknown_since.pop(inst_id, None)
                    unknown_streak[inst_id] = 0
                    _write_detected_screen(
                        redis_client,
                        instance_id=inst_id,
                        screen=screen,
                    )
                    continue

                unknown_streak[inst_id] = unknown_streak.get(inst_id, 0) + 1
                now_mono = time.monotonic()
                unknown_since.setdefault(inst_id, now_mono)
                last_seen_at = last_detected_screen_at.get(inst_id, 0.0)
                unknown_age = now_mono - unknown_since[inst_id]
                should_clear = (
                    last_seen_at <= 0.0
                    or (
                        unknown_streak[inst_id] >= _SCREEN_UNKNOWN_CLEAR_AFTER_FRAMES
                        and unknown_age >= _SCREEN_UNKNOWN_CLEAR_AFTER_SECONDS
                    )
                )
                if not should_clear:
                    continue

                screen = ""
                last_detected_screen.pop(inst_id, None)
                last_detected_screen_at.pop(inst_id, None)
                unknown_since.pop(inst_id, None)
                _write_detected_screen(
                    redis_client,
                    instance_id=inst_id,
                    screen=screen,
                )
            except Exception:
                logger.exception(
                    "IA preview refresher: screen detection failed for %s",
                    inst.instance_id,
                )

        elapsed = time.monotonic() - started
        time.sleep(max(0.1, interval - elapsed))


def ensure_ia_preview_refresher() -> None:
    """Start the IA Editor screenshot refresher once per Streamlit process."""

    if _existing_preview_thread() is not None:
        return
    thread = threading.Thread(target=_preview_loop, daemon=True, name=_THREAD_NAME)
    thread.start()

