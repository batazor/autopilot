"""Pure helpers for click-approval surfacing (API-side preview + labeling deep-link).

Extracted from the deleted ``src/ui/views/click_approvals/`` Streamlit pages so
the FastAPI services can build the same payloads without dragging Streamlit
into the import graph.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from dashboard.redis_client import get_instance_state
from dsl import template_resolver as _tmpl
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region, region_version_of

_AREA_DOC_TTL_S = 60.0
_area_doc_cache: dict[tuple[str, float], tuple[float, dict[str, Any]]] = {}


def scenario_display_name(scenario_key: str) -> str:
    """Resolve a scenario key to its rendered YAML ``name:`` field."""
    repo_root = Path(__file__).resolve().parents[2]
    return _tmpl.display_name(repo_root, scenario_key)


def load_area_doc(area_path: Path) -> dict[str, Any]:
    """Read ``area.json`` with a 60s mtime-keyed cache.

    Previously ``@st.cache_data(ttl=60)``; now a small TTL-respecting dict so
    every API request doesn't re-read the file.
    """
    try:
        mtime = area_path.stat().st_mtime if area_path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    key = (str(area_path), mtime)
    now = time.monotonic()
    cached = _area_doc_cache.get(key)
    if cached is not None and (now - cached[0]) < _AREA_DOC_TTL_S:
        return cached[1]
    if not area_path.is_file():
        doc: dict[str, Any] = {}
    else:
        try:
            doc = json.loads(area_path.read_text(encoding="utf-8"))
        except Exception:
            doc = {}
    _area_doc_cache[key] = (now, doc)
    return doc


def labeling_query_ref_from_area_ocr(ocr_rel: str) -> str | None:
    """Path under ``references/`` for Labeling ``?ref=``."""
    s = (ocr_rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return None
    if s.startswith("references/"):
        s = s.removeprefix("references/")
    return s or None


def labeling_query_params_for_area_region(
    area_doc: dict[str, Any],
    region_name: str,
    *,
    state_flat: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Build a Labeling deep-link for a logical area region."""
    pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
    if pair is None:
        return None
    entry, reg = pair
    ref_rel = effective_ocr_for_region(entry, reg)
    lbl_ref = labeling_query_ref_from_area_ocr(ref_rel)
    if not lbl_ref:
        return None

    resolved_region = str(reg.get("name") or "").strip() or str(region_name or "").strip()
    params = {"ref": lbl_ref}
    if resolved_region:
        params["region"] = resolved_region

    vid = region_version_of(entry, reg)
    if vid:
        params["version"] = vid
    return params


def active_player_state_flat(*, client: Any, instance_id: str) -> dict[str, Any] | None:
    """Flat per-player state dict for the instance's active player, or ``None`` if absent.

    Used by region-by-name lookups that must honor screen-version ``cond``
    selection — without state, lookups silently fall back to the default version,
    so click_approvals would surface stale v1 regions on accounts on v2/v3.
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


def pct_bbox_to_px_rect(bb: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    bw = float(bb.get("width") or 0.0)
    bh = float(bb.get("height") or 0.0)
    left = max(0, min(w - 1, int(x / 100.0 * w)))
    top = max(0, min(h - 1, int(y / 100.0 * h)))
    right = max(left + 1, min(w, int((x + bw) / 100.0 * w)))
    bottom = max(top + 1, min(h, int((y + bh) / 100.0 * h)))
    return left, top, right, bottom


def approval_region_name(payload: dict[str, Any], ctx0: dict[str, Any]) -> str:
    """Region label for the pending input; prefer explicit request data over task context."""
    try:
        reg_name = str(payload.get("region") or "").strip()
    except Exception:
        reg_name = ""
    if not reg_name:
        reg_name = str(ctx0.get("approval_region") or "").strip()
    if not reg_name:
        reg_name = str(ctx0.get("current_task_region") or "").strip()
    return reg_name


_approval_region_name = approval_region_name
