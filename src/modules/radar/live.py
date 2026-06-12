"""Live stitching: rebuild the map periodically mid-scan.

The scan loop saves a frame every few seconds; the thread here notices new
files and re-runs the ORB stitcher over everything captured so far, then
publishes ``map_updated`` so the /radar page refreshes its live preview.

A full re-stitch is expensive (it re-reads every frame and recomputes ORB), so
the loop *coalesces*: it waits until either a minimum interval has elapsed or a
batch of new frames has accumulated before re-stitching, rather than firing on
every single new file. The live preview is also rendered at a reduced
resolution — operators only need a rough live view; the final pass after the
scan produces the full-resolution map.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Protocol

from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import run_stitch

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.5
_JOIN_TIMEOUT_S = 120.0
# Coalescing: don't re-stitch more often than this, and don't wait longer than
# this once even a single new frame has appeared. Bounds wasted re-stitch work
# on big scans while keeping the live preview responsive.
_MIN_RESTITCH_INTERVAL_S = 8.0
# ...unless this many new frames piled up, then re-stitch immediately so the
# preview never falls far behind on a fast device.
_RESTITCH_BATCH = 6
# Reduced live preview long-side (px): cheap to encode, plenty for a live view.
_LIVE_PREVIEW_LONG_SIDE = 2048


class _MapPublisher(Protocol):
    def map_updated(self, frames: int) -> None: ...


@contextlib.contextmanager
def live_stitching(run_dir: Path, publisher: _MapPublisher) -> Iterator[None]:
    """Background re-stitch while the wrapped scan captures frames.

    The caller still owns the final stitch + tile build after the scan ends;
    exiting the context only stops the loop. ``run_stitch`` serializes per run
    internally, so even if the in-flight pass outlives the join timeout it can
    never corrupt the final pass's output — it only logs a warning here.
    """
    stop = threading.Event()

    def loop() -> None:
        stitched = 0
        # 0.0 → the first batch of frames stitches promptly; only subsequent
        # re-stitches are rate-limited by _MIN_RESTITCH_INTERVAL_S.
        last_stitch = 0.0
        while not stop.wait(_POLL_INTERVAL_S):
            if not (run_dir / MANIFEST_NAME).is_file():
                continue
            count = len(list(run_dir.glob("frame_*.png")))
            new = count - stitched
            if new <= 0:
                continue
            # Coalesce: hold off until enough frames accumulate or enough time
            # has passed, so a fast capture rate doesn't queue a full re-stitch
            # per frame.
            elapsed = time.monotonic() - last_stitch
            if new < _RESTITCH_BATCH and elapsed < _MIN_RESTITCH_INTERVAL_S:
                continue
            try:
                run_stitch(run_dir, preview_long_side=_LIVE_PREVIEW_LONG_SIDE)
                publisher.map_updated(count)
            except Exception:
                logger.exception("radar: live stitch failed (%s)", run_dir.name)
            # Advance even on failure so a bad frame set cannot spin the loop;
            # the next captured frame triggers another attempt anyway.
            stitched = count
            last_stitch = time.monotonic()

    thread = threading.Thread(target=loop, name=f"radar-live-{run_dir.name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_JOIN_TIMEOUT_S)
        if thread.is_alive():
            logger.warning(
                "radar: live stitch thread for %s still running after %.0fs join "
                "timeout — the final stitch is serialized against it, so output "
                "stays consistent, but the scan teardown did not wait for it",
                run_dir.name, _JOIN_TIMEOUT_S,
            )
