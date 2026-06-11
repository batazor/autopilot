"""Live stitching: rebuild the map after every captured frame, mid-scan.

The scan loop saves a frame every few seconds; the thread here notices new
files, re-runs the ORB stitcher over everything captured so far and publishes
``map_updated`` so the /radar page refreshes its live preview. Stitching is
strictly sequential — a slow pass simply batches several frames into the next
one, so the scan itself is never throttled.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING, Protocol

from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import run_stitch

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.5
_JOIN_TIMEOUT_S = 120.0


class _MapPublisher(Protocol):
    def map_updated(self, frames: int) -> None: ...


@contextlib.contextmanager
def live_stitching(run_dir: Path, publisher: _MapPublisher) -> Iterator[None]:
    """Background re-stitch while the wrapped scan captures frames.

    The caller still owns the final stitch + tile build after the scan ends;
    exiting the context only stops the loop (joining the in-flight pass so a
    half-finished live stitch can never race the final one).
    """
    stop = threading.Event()

    def loop() -> None:
        stitched = 0
        while not stop.wait(_POLL_INTERVAL_S):
            if not (run_dir / MANIFEST_NAME).is_file():
                continue
            count = len(list(run_dir.glob("frame_*.png")))
            if count <= stitched:
                continue
            try:
                run_stitch(run_dir)
                publisher.map_updated(count)
            except Exception:
                logger.exception("radar: live stitch failed (%s)", run_dir.name)
            # Advance even on failure so a bad frame set cannot spin the loop;
            # the next captured frame triggers another attempt anyway.
            stitched = count

    thread = threading.Thread(target=loop, name=f"radar-live-{run_dir.name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_JOIN_TIMEOUT_S)
