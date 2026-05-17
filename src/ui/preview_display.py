"""Resize PNG previews so tall emulator screenshots fit the page."""
from __future__ import annotations

from io import BytesIO

from PIL import Image


def png_bytes_fitted(data: bytes, max_side: int) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    """

    Downscale PNG bytes so max(width, height) <= max_side; smaller images unchanged.

    Returns (png_bytes, native_size (w,h), displayed_size (w,h)).
    """
    if max_side < 16:
        max_side = 16
    im = Image.open(BytesIO(data))
    native = im.size
    w, h = native
    if max(w, h) <= max_side:
        return data, native, native
    im_copy = im.copy()
    im_copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = BytesIO()
    im_copy.save(out, format="PNG")
    disp = im_copy.size
    return out.getvalue(), native, disp
