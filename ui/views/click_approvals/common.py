from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

from ui.redis_client import get_instance_state

_VERSION_SUFFIX_RE = re.compile(r"_v\d+$")


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


def active_player_state_flat(*, client: Any, instance_id: str) -> dict[str, Any] | None:
    """Flat per-player state dict for the instance's active player, or ``None`` if absent.

    Used by region-by-name lookups that must honor screen-version `cond` selection — without
    state, lookups silently fall back to the default version, which means click_approvals
    would surface stale v1 regions on accounts that have transitioned to v2/v3.
    """
    try:
        row = get_instance_state(client, instance_id) or {}
    except Exception:
        return None
    active = str(row.get("active_player") or "").strip()
    if not active:
        return None
    try:
        from config.state_store import get_state_store

        return get_state_store().get_or_create(active).to_flat_dict()
    except Exception:
        return None


def has_version_suffix(name: str) -> bool:
    """``True`` if ``name`` ends with a ``_vN`` version suffix (``promote_btn_v2``, ``label_v3``)."""
    return bool(_VERSION_SUFFIX_RE.search((name or "").strip()))


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

