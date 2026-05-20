from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import streamlit as st

from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region, region_version_of
from dsl import template_resolver as _tmpl
from ui.redis_client import get_instance_state


def scenario_display_name(scenario_key: str) -> str:
    """Resolve a scenario key to its rendered YAML ``name:`` field.

    Goes through ``scenarios.template_resolver`` so template-driven keys like
    ``level_up_ahmose`` get the substituted name (``"⬆️ Level up · Ahmose"``)
    instead of the raw key. Falls back to the key when the scenario YAML is
    missing or has no ``name``.
    """
    repo_root = Path(__file__).resolve().parents[3]
    return _tmpl.display_name(repo_root, scenario_key)


@st.cache_data(ttl=60)
def load_area_doc_cached(area_path: Path, mtime: float) -> dict[str, Any]:
    """Cache `area.json` keyed by file mtime."""
    if not area_path.is_file():
        return {}
    try:
        return json.loads(area_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_area_doc(area_path: Path) -> dict[str, Any]:
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


@st.cache_data(ttl=5)
def ocr_health_status(tesseract_cmd: str) -> tuple[bool, str]:
    cmd = str(tesseract_cmd or "tesseract").strip() or "tesseract"
    if shutil.which(cmd):
        return True, "ok"
    return False, f"tesseract executable not found: {cmd}"
