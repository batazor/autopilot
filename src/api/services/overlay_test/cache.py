"""Frame loading + screen-detect hint/result caches for overlay-test probes."""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from api.services.click_approval_overlay import (
    load_preview_bytes,
)
from api.services.overlay_test.common import _detect_screen_on_frame
from config.paths import repo_root
from dashboard.reference_preview import load_rolling_instance_preview
from layout.area_manifest import (
    AreaManifestFingerprint,
    area_manifest_fingerprint,
)

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


# Process-local cache: maps ``instance_id`` to the last detected screen for that
# overlay-test session. Used as ``hint`` for the next probe when Redis has no
# live ``current_screen`` (worker not running) — keeps the sticky verify fast
# path warm across probes instead of always falling back to a cold full scan.
_overlay_test_hint_cache_lock = threading.Lock()
_overlay_test_hint_cache: dict[str, str] = {}


def _overlay_test_remember_hint(instance_id: str, detected: str) -> None:
    if not instance_id or not detected:
        return
    with _overlay_test_hint_cache_lock:
        _overlay_test_hint_cache[instance_id] = detected


def _overlay_test_recall_hint(instance_id: str) -> str | None:
    if not instance_id:
        return None
    with _overlay_test_hint_cache_lock:
        return _overlay_test_hint_cache.get(instance_id)


# Process-local LRU of recent detection results keyed by ``(frame_hash, area_fingerprint)``.
# Hashing a 720×1280 BGR frame with blake2b runs in ~3–5 ms — orders of magnitude
# cheaper than re-running the full multi-screen scan. The area fingerprint
# invalidates the cache when any merged area manifest changes.
_OVERLAY_TEST_RESULT_CACHE_MAX = 32
_overlay_test_result_cache_lock = threading.Lock()
_overlay_test_result_cache: OrderedDict[
    tuple[bytes, AreaManifestFingerprint], str
] = OrderedDict()


def _overlay_test_frame_fingerprint(png_bytes: bytes | None) -> bytes | None:
    """Cheap content hash for the PNG payload backing this probe.

    Hashing the raw PNG bytes (not the decoded BGR array) avoids a cv2.imdecode
    pass when the cache hits. The Next.js dashboard re-polls overlay-test with
    the same preview PNG until the worker captures a new frame, so the hit rate
    is very high for steady-state probes.
    """
    if not png_bytes:
        return None
    return hashlib.blake2b(png_bytes, digest_size=16).digest()


def _overlay_test_result_cache_get(
    fingerprint: bytes | None,
    area_fingerprint: AreaManifestFingerprint,
) -> str | None:
    if fingerprint is None:
        return None
    key = (fingerprint, area_fingerprint)
    with _overlay_test_result_cache_lock:
        cached = _overlay_test_result_cache.get(key)
        if cached is not None:
            _overlay_test_result_cache.move_to_end(key)
        return cached


def _overlay_test_result_cache_put(
    fingerprint: bytes | None,
    area_fingerprint: AreaManifestFingerprint,
    detected: str,
) -> None:
    if fingerprint is None or not detected:
        return
    key = (fingerprint, area_fingerprint)
    with _overlay_test_result_cache_lock:
        _overlay_test_result_cache[key] = detected
        _overlay_test_result_cache.move_to_end(key)
        while len(_overlay_test_result_cache) > _OVERLAY_TEST_RESULT_CACHE_MAX:
            _overlay_test_result_cache.popitem(last=False)


def _load_overlay_test_preview(
    *,
    instance_id: str,
    preview_source: str = "live",
    preview_rel: str | None = None,
) -> tuple[bytes | None, str, float | None, str]:
    """Load the frame for overlay-test (rolling live or a repo-relative reference PNG)."""
    src = (preview_source or "live").strip().lower()
    if src == "reference":
        rel = (preview_rel or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            return None, "", None, "reference"
        try:
            from api.services.gallery_api import read_gallery_image

            png = read_gallery_image(rel)
            path = (repo_root() / rel).resolve()
            mtime = float(path.stat().st_mtime) if path.is_file() else None
            return png, rel, mtime, "reference"
        except (ValueError, FileNotFoundError, OSError):
            return None, rel, None, "reference"
    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)
    return png, rel or "", mtime, "live"


def _screen_detect_hint(
    *,
    client: Any | None,
    instance_id: str,
) -> str | None:
    """Current-screen hint for static-frame screen detection."""
    screen_hint: str | None = None
    if client is not None:
        try:
            from dashboard.redis_client import get_instance_state

            inst_state = get_instance_state(client, instance_id) or {}
            hint_raw = str(inst_state.get("current_screen") or "").strip()
            screen_hint = hint_raw or None
        except Exception:
            logger.debug("overlay-test: hint lookup failed", exc_info=True)

    # When the live worker isn't running, Redis ``current_screen`` is empty and
    # every probe pays the cold-path scan. Fall back to the last detection we
    # saw for this overlay-test session so the sticky verify path can take over.
    if screen_hint is None:
        screen_hint = _overlay_test_recall_hint(instance_id)
    return screen_hint


def _detect_screen_from_preview_png(
    *,
    instance_id: str,
    client: Any | None,
    png: bytes | None,
    image_bgr: np.ndarray | None,
) -> tuple[str, int]:
    """Detect screen on a loaded preview PNG using the shared hash/hint cache."""
    screen_hint = _screen_detect_hint(client=client, instance_id=instance_id)

    # Content-hash cache: when the dashboard repolls with the same preview PNG
    # (worker hasn't captured a fresh frame yet), skip the full scan entirely.
    frame_fp = _overlay_test_frame_fingerprint(png)
    area_fingerprint_for_cache: AreaManifestFingerprint | None = None
    if frame_fp is not None:
        area_fingerprint_for_cache = area_manifest_fingerprint(repo_root())
        cached_detected = _overlay_test_result_cache_get(
            frame_fp, area_fingerprint_for_cache
        )
        if cached_detected is not None:
            return cached_detected, 0

    detected_screen, screen_detect_ms = _detect_screen_on_frame(
        image_bgr, hint=screen_hint
    )
    if detected_screen and area_fingerprint_for_cache is not None:
        _overlay_test_result_cache_put(
            frame_fp, area_fingerprint_for_cache, detected_screen
        )
        _overlay_test_remember_hint(instance_id, detected_screen)
    return detected_screen, screen_detect_ms
