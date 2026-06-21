"""Scan progress over Redis: pub/sub channel ``radar:events`` + the active-scan key.

``radar:scan_active`` is the single source of truth for "a scan is queued or
running": the API sets it (NX) when enqueuing, the scanner refreshes it on
every frame and clears it at the end. The TTL means a crashed scanner can
never wedge new scans forever.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from modules.radar.config import DEFAULT_TARGET

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

CHANNEL = "radar:events"
STREAM = "radar:events_stream"
ACTIVE_KEY = "radar:scan_active"
# Stop request: holds the run_id the operator asked to stop. The scanner polls
# it each frame and ends the run gracefully (partial frames still get stitched).
STOP_KEY = "radar:scan_stop"
ACTIVE_TTL_S = 900
STREAM_MAXLEN = 2048


def request_stop(client: Any, run_id: str) -> None:
    """Ask the scanner to stop ``run_id`` after the current frame."""
    try:
        client.set(STOP_KEY, run_id, ex=ACTIVE_TTL_S)
    except Exception:
        logger.warning("radar: stop-key write failed", exc_info=True)


def stop_requested(client: Any, run_id: str) -> bool:
    """True once a stop has been requested for ``run_id``."""
    try:
        return client.get(STOP_KEY) == run_id
    except Exception:
        logger.warning("radar: stop-key read failed", exc_info=True)
        return False


def clear_stop(client: Any) -> None:
    try:
        client.delete(STOP_KEY)
    except Exception:
        logger.warning("radar: stop-key clear failed", exc_info=True)


def read_active(client: Any) -> dict | None:
    """Current active-scan state (``{run_id, status, done, total}``) or None."""
    try:
        raw = client.get(ACTIVE_KEY)
    except Exception:
        logger.warning("radar: active-key read failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def set_active(
    client: Any,
    run_id: str,
    status: str,
    *,
    done: int = 0,
    total: int = 0,
    grid: Iterable[dict[str, int]] | None = None,
    target: str = DEFAULT_TARGET,
    only_if_absent: bool = False,
) -> bool:
    current_grid = grid
    if current_grid is None and not only_if_absent:
        active = read_active(client) or {}
        raw_grid = active.get("grid")
        if isinstance(raw_grid, list):
            current_grid = raw_grid
    data: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "done": done,
        "total": total,
        "target": target,
    }
    if current_grid is not None:
        data["grid"] = list(current_grid)
    payload = json.dumps(data)
    try:
        if only_if_absent:
            return bool(client.set(ACTIVE_KEY, payload, nx=True, ex=ACTIVE_TTL_S))
        return bool(client.set(ACTIVE_KEY, payload, ex=ACTIVE_TTL_S))
    except Exception:
        logger.warning("radar: active-key write failed", exc_info=True)
        return False


def clear_active(client: Any) -> None:
    try:
        client.delete(ACTIVE_KEY)
    except Exception:
        logger.warning("radar: active-key clear failed", exc_info=True)


class RadarEventPublisher:
    """Publishes the scan lifecycle for one run and maintains the active key.

    Every method is fire-and-forget: a Redis flap mid-scan must never kill a
    5-minute capture run, so failures are logged and swallowed.
    """

    def __init__(self, client: Any, run_id: str, target: str = DEFAULT_TARGET) -> None:
        self._client = client
        self.run_id = run_id
        self.target = target

    def stop_requested(self) -> bool:
        """True when the operator has asked to stop this run."""
        return stop_requested(self._client, self.run_id)

    def _publish(self, payload: dict[str, Any]) -> None:
        # Every event carries (run_id, target) so the dashboard can route live
        # progress to the right map tab without an extra lookup.
        event = {"run_id": self.run_id, "target": self.target, **payload}
        try:
            self._client.xadd(
                STREAM,
                {"data": json.dumps(event, ensure_ascii=False)},
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
        except Exception:
            logger.warning("radar: event stream write failed (%s)", payload.get("type"), exc_info=True)
        try:
            self._client.publish(CHANNEL, json.dumps(event, ensure_ascii=False))
        except Exception:
            logger.warning("radar: event publish failed (%s)", payload.get("type"), exc_info=True)

    def scan_started(self, total: int, grid: Iterable[tuple[int, int]]) -> None:
        clear_stop(self._client)  # drop any stale stop flag from a prior run
        cells = [{"ix": ix, "iy": iy} for ix, iy in grid]
        set_active(
            self._client, self.run_id, "scanning",
            done=0, total=total, grid=cells, target=self.target,
        )
        self._publish({"type": "scan_started", "total_frames": total, "grid": cells})

    def frame_done(self, ix: int, iy: int, *, unstable: bool, done: int, total: int) -> None:
        set_active(
            self._client, self.run_id, "scanning",
            done=done, total=total, target=self.target,
        )
        self._publish(
            {
                "type": "frame_done",
                "ix": ix,
                "iy": iy,
                "unstable": unstable,
                "done": done,
                "total": total,
            }
        )

    def scan_finished(self, duration_s: float, *, stopped: bool = False) -> None:
        clear_active(self._client)
        clear_stop(self._client)
        self._publish(
            {"type": "scan_finished", "duration_s": round(duration_s, 1), "stopped": stopped}
        )

    def scan_failed(self, error: str) -> None:
        clear_active(self._client)
        clear_stop(self._client)
        self._publish({"type": "scan_failed", "error": error})

    def map_updated(self, frames: int) -> None:
        """Live preview re-stitched with ``frames`` frames — UI should refetch it."""
        self._publish({"type": "map_updated", "frames": frames})

    def tiles_ready(self) -> None:
        self._publish({"type": "tiles_ready"})
