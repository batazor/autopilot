"""Conditional (ETag/304) responses for disk-backed preview images.

The rolling preview PNGs are rewritten by the worker every ~1s while active,
but pollers (approvals page, live editor, extra dashboard tabs) frequently ask
again while the frame on disk is unchanged — deep idle drops the rewrite
cadence to ~5s, and an approval freeze stops it entirely. An ETag derived from
``(mtime_ns, size)`` lets those repeat polls return 304 without reading or
sending the image. ``Cache-Control: no-cache`` (not ``no-store``) forces the
browser to revalidate on every use — no stale frames — while still allowing
the conditional round-trip.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import Response

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import Request

_CACHE_CONTROL = "no-cache, max-age=0"


def conditional_png_response(path: Path | None, request: Request) -> Response | None:
    """Serve ``path`` as PNG with ETag revalidation; ``None`` when unreadable.

    The stat comes from ``fstat`` on the already-open handle so the ETag always
    matches the bytes actually served, even if the worker replaces the file
    between requests.
    """
    if path is None:
        return None
    try:
        with path.open("rb") as f:
            st = os.fstat(f.fileno())
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}
            if etag in (request.headers.get("if-none-match") or ""):
                return Response(status_code=304, headers=headers)
            return Response(content=f.read(), media_type="image/png", headers=headers)
    except OSError:
        return None
