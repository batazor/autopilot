from __future__ import annotations

import sys
from typing import Any

import numpy as np


class QuartzCapture:
    """Screen capture for BlueStacks windows via macOS Quartz."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Screen capture is only supported on macOS")
        import Quartz as _quartz  # type: ignore[import-untyped]

        self._q: Any = _quartz

    def find_window(self, title: str) -> int:
        q = self._q
        window_list = q.CGWindowListCopyWindowInfo(
            q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
            q.kCGNullWindowID,
        )
        for info in window_list:
            owner = info.get("kCGWindowOwnerName", "")
            name = info.get("kCGWindowName", "")
            if title.lower() in owner.lower() or title.lower() in name.lower():
                return int(info["kCGWindowNumber"])
        raise ValueError(f"Window not found: {title!r}")

    def capture(self, window_id: int) -> np.ndarray:
        q = self._q
        image_ref = q.CGWindowListCreateImage(
            q.CGRectNull,
            q.kCGWindowListOptionIncludingWindow,
            window_id,
            q.kCGWindowImageBoundsIgnoreFraming,
        )
        if image_ref is None:
            raise PermissionError(
                "Screen capture failed. Grant Screen Recording permission in "
                "System Settings → Privacy & Security → Screen Recording."
            )

        width = q.CGImageGetWidth(image_ref)
        height = q.CGImageGetHeight(image_ref)
        bpp = q.CGImageGetBitsPerPixel(image_ref)
        bpr = q.CGImageGetBytesPerRow(image_ref)

        data_provider = q.CGImageGetDataProvider(image_ref)
        raw_data = q.CGDataProviderCopyData(data_provider)
        buf = np.frombuffer(raw_data, dtype=np.uint8)

        channels = bpp // 8
        image = buf.reshape((height, bpr // channels, channels))
        image = image[:, :width, :]

        # Quartz returns BGRA — drop alpha, keep BGR for OpenCV compatibility
        return image[:, :, :3]

    def list_bluestacks_windows(self) -> list[str]:
        q = self._q
        window_list = q.CGWindowListCopyWindowInfo(
            q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
            q.kCGNullWindowID,
        )
        titles: list[str] = []
        for info in window_list:
            owner = info.get("kCGWindowOwnerName", "")
            name = info.get("kCGWindowName", "")
            combined = f"{owner} {name}".lower()
            if "bluestacks" in combined:
                titles.append(f"{owner}: {name}")
        return titles
