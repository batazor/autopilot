from __future__ import annotations

import json
from pathlib import Path

import httpx
import streamlit as st


@st.cache_data(ttl=60)
def load_area_doc_cached(area_path: Path, mtime: float) -> dict[str, object]:
    """Cache `area.json` keyed by file mtime."""
    if not area_path.is_file():
        return {}
    try:
        return json.loads(area_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_area_doc(area_path: Path) -> dict[str, object]:
    try:
        mtime = area_path.stat().st_mtime if area_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return load_area_doc_cached(area_path, mtime)


def labeling_query_ref_from_area_ocr(ocr_rel: str) -> str | None:
    """Path under `references/` for Labeling `?ref=`."""
    s = (ocr_rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return None
    if s.startswith("references/"):
        s = s.removeprefix("references/")
    return s or None


@st.cache_data(ttl=5)
def ocr_health_status(ocr_url: str) -> tuple[bool, str]:
    url = str(ocr_url or "").strip()
    if not url:
        return False, "OCR url is not configured"
    try:
        with httpx.Client(timeout=1.0) as c:
            r = c.get(f"{url}/health")
            r.raise_for_status()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - UI diagnostic only
        return False, f"{type(exc).__name__}: {exc}"

