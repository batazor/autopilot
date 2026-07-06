"""ETag revalidation for disk-backed preview images."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from starlette.requests import Request

from api.services.http_cache import conditional_png_response

if TYPE_CHECKING:
    from pathlib import Path


def _request(headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


def test_first_fetch_returns_body_with_etag(tmp_path: Path) -> None:
    p = tmp_path / "preview.png"
    p.write_bytes(b"\x89PNG-fake-frame")
    resp = conditional_png_response(p, _request())
    assert resp is not None
    assert resp.status_code == 200
    assert resp.body == b"\x89PNG-fake-frame"
    assert resp.headers["ETag"].startswith('"')
    # ``no-store`` would stop the browser from ever sending If-None-Match.
    assert "no-store" not in resp.headers["Cache-Control"]
    assert "no-cache" in resp.headers["Cache-Control"]


def test_matching_etag_returns_304_without_body(tmp_path: Path) -> None:
    p = tmp_path / "preview.png"
    p.write_bytes(b"\x89PNG-fake-frame")
    first = conditional_png_response(p, _request())
    assert first is not None
    etag = first.headers["ETag"]
    resp = conditional_png_response(p, _request({"if-none-match": etag}))
    assert resp is not None
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers["ETag"] == etag


def test_rewritten_file_invalidates_etag(tmp_path: Path) -> None:
    p = tmp_path / "preview.png"
    p.write_bytes(b"frame-one")
    first = conditional_png_response(p, _request())
    assert first is not None
    p.write_bytes(b"frame-two!")
    os.utime(p, ns=(1, 1))  # distinct mtime even on coarse filesystem clocks
    resp = conditional_png_response(p, _request({"if-none-match": first.headers["ETag"]}))
    assert resp is not None
    assert resp.status_code == 200
    assert resp.body == b"frame-two!"
    assert resp.headers["ETag"] != first.headers["ETag"]


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert conditional_png_response(tmp_path / "gone.png", _request()) is None
    assert conditional_png_response(None, _request()) is None
